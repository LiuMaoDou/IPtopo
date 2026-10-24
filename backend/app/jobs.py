"""Persisted import jobs executed in a separate spawned process."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import shutil
import time
from sqlalchemy import select
from .store import Job, Project, Snapshot, Source, Store, now, uid

PROGRESS_BATCH_SIZE = 25
PROGRESS_INTERVAL_SECONDS = 0.25


def empty_snapshot(project_id: str) -> dict:
    return {"id": uid("snap_"), "project_id": project_id, "created_at": now(),
            "devices": [], "interfaces": [], "contexts": [], "source_files": [],
            "diagnostics": [], "segments": [], "links": [], "unmatched": [], "parser_versions": {}}


def import_worker(data_dir: str, job_id: str):
    # Imported only inside the worker so multiprocessing never shares DB connections.
    from .parser import parse_config
    from .overrides import apply_overrides
    store = Store(data_dir)
    started = time.perf_counter()
    pending_results = []
    progress = 0
    last_flush = started
    last_cancel_check = 0.0

    def flush_progress():
        """Persist a bounded batch without replacing concurrently requested cancellation."""
        nonlocal last_flush
        if not pending_results:
            return
        with store.session(write=True) as s:
            job = s.get(Job, job_id)
            if job:
                job.progress = max(job.progress, progress)
                job.updated_at = now()
                job.data = {**job.data, "results": [*job.data.get("results", []), *pending_results]}
        pending_results.clear()
        last_flush = time.perf_counter()

    try:
        with store.session(write=True) as s:
            job = s.get(Job, job_id)
            if not job or job.status not in {"queued", "running"}:
                return
            job.status = "running"
            job.updated_at = now()
            project = s.get(Project, job.project_id)
            old = s.get(Snapshot, project.snapshot_id) if project.snapshot_id else None
            old_data = deepcopy(old.data) if old else empty_snapshot(project.id)
            settings = deepcopy(project.settings)
            job_data = deepcopy(job.data)
            project_id = project.id
        result = empty_snapshot(project_id)
        result["import_batch"] = job_id
        result["domain_settings"] = deepcopy(settings.get("domains", {}))
        identities = settings.get("identities", {})
        previous_files = {f["filename"]: f for f in old_data.get("source_files", []) if f.get("parse_status") != "failed" and f.get("device_id")}
        devices_old = {d["id"]: d for d in old_data.get("devices", [])}
        replacements, failures = set(), set()
        parsed_devices = []
        source_rows = []
        total = len(job_data.get("uploads", []))
        for index, upload in enumerate(job_data.get("uploads", [])):
            current_time = time.perf_counter()
            # Check at file boundaries every 250 ms. A single parser invocation
            # remains cooperative; large files cannot be interrupted mid-parse.
            if current_time - last_cancel_check >= PROGRESS_INTERVAL_SECONDS:
                last_cancel_check = current_time
                with store.session() as s:
                    running = s.get(Job, job_id)
                    cancelled = not running or running.status in {"cancelling", "cancelled", "interrupted"}
                if cancelled:
                    flush_progress()
                    with store.session(write=True) as ws:
                        j = ws.get(Job, job_id)
                        if j and j.status != "interrupted":
                            j.status = "cancelled"
                            j.updated_at = now()
                    return
            filename = upload["filename"]
            source_id = uid("src_")
            device_id = identities.get(filename, uid("dev_"))
            raw = (store.root / "uploads" / job_id / upload["storage_key"]).read_text(encoding="utf-8")
            try:
                parsed = parse_config(raw, filename, source_id, device_id, job_data.get("dialect") or None)
                # A filename is an explicit input mapping, not proof that a replacement is the same device.
                previous = devices_old.get(device_id)
                current = parsed.get("device")
                if previous and current and (previous.get("hostname"), previous.get("vendor")) != (current.get("hostname"), current.get("vendor")):
                    device_id = uid("dev_")
                    parsed = parse_config(raw, filename, source_id, device_id, job_data.get("dialect") or None)
                    parsed.setdefault("diagnostics", []).append({"id": uid("diag_"), "code": "identity_changed", "severity": "warning", "source_file_id": source_id,
                        "message": "同名文件的设备名称或厂商已变化，已创建新的设备身份；旧人工修正需要复核。"})
                    parsed["status"] = "partial" if parsed.get("status") != "failed" else "failed"
                    if parsed.get("device"):
                        parsed["device"]["parse_status"] = parsed["status"]
                status = parsed.get("status", "failed")
                interfaces = parsed.get("interfaces", [])
                addresses = [a for itf in interfaces for a in itf.get("addresses", [])]
                meta = {"id": source_id, "filename": filename, "hash": upload["hash"], "encoding": upload["encoding"],
                    "parse_status": status, "status": status, "vendor": parsed.get("vendor", "unknown"), "os": parsed.get("os", "unknown"),
                    "format": parsed.get("format", "unknown"), "device_id": device_id if parsed.get("device") else None,
                    "hostname": (parsed.get("device") or {}).get("hostname", ""), "interface_count": len(interfaces),
                    "ipv4_count": sum(a.get("family") == 4 and bool(a.get("address")) for a in addresses),
                    "ipv6_count": sum(a.get("family") == 6 and bool(a.get("address")) for a in addresses),
                    "context_count": len(parsed.get("contexts", [])), "diagnostics": parsed.get("diagnostics", [])}
                meta["warning_count"] = len(meta["diagnostics"])
                result["diagnostics"].extend(meta["diagnostics"])
                if status != "failed" and parsed.get("device"):
                    replacements.add(filename)
                    identities[filename] = device_id
                    result["devices"].append(parsed["device"])
                    result["interfaces"].extend(interfaces)
                    result["contexts"].extend(parsed.get("contexts", []))
                    result["source_files"].append(meta)
                    result["parser_versions"].update(parsed.get("parser_versions", {}))
                    parsed_devices.append(parsed["device"])
                else:
                    failures.add(filename)
                    result["source_files"].append(meta)
            except Exception as exc:
                # Do not expose raw config or exception strings that may contain credentials.
                failures.add(filename)
                meta = {"id": source_id, "filename": filename, "parse_status": "failed", "status": "failed", "diagnostics": [
                    {"id": uid("diag_"), "code": "parser_exception", "severity": "error", "source_file_id": source_id,
                     "message": f"解析器异常 ({type(exc).__name__})；其他文件仍会继续处理。"}]}
                result["source_files"].append(meta)
                result["diagnostics"].extend(meta["diagnostics"])
            storage_key = uid() + ".txt"
            (store.root / "sources" / storage_key).write_text(raw, encoding="utf-8")
            source_rows.append({"key": uid(), "project_id": project_id, "source_id": source_id, "filename": filename,
                                "storage_key": storage_key, "encoding": upload["encoding"]})
            pending_results.append(meta)
            progress = round((index + 1) / max(total, 1) * 90)
            if len(pending_results) >= PROGRESS_BATCH_SIZE or time.perf_counter() - last_flush >= PROGRESS_INTERVAL_SECONDS:
                flush_progress()
        flush_progress()
        # Merge retains all files not successfully replaced; replace still protects failed files.
        keep_names = (set(previous_files) - replacements) if job_data.get("strategy", "merge") == "merge" else (set(previous_files) & failures)
        keep_device_ids = {previous_files[n].get("device_id") for n in keep_names}
        keep_sources = {previous_files[n]["id"] for n in keep_names}
        for key in ["devices", "interfaces", "contexts"]:
            result[key] = [obj for obj in old_data.get(key, []) if (obj.get("id") if key == "devices" else obj.get("device_id")) in keep_device_ids] + result[key]
        result["source_files"] = [f for f in old_data.get("source_files", []) if f["id"] in keep_sources] + result["source_files"]
        result["diagnostics"] = [d for d in old_data.get("diagnostics", []) if d.get("source_file_id") in keep_sources] + result["diagnostics"]
        result["parser_versions"] = {**old_data.get("parser_versions", {}), **result["parser_versions"]}
        duplicate_names = {name for name, n in Counter(d["hostname"] for d in result["devices"]).items() if n > 1}
        for name in sorted(duplicate_names):
            result["diagnostics"].append({"id": uid("diag_"), "code": "duplicate_hostname", "severity": "warning", "message": f"设备名称 {name} 重复，已保留独立设备身份。"})
        with store.session(write=True) as s:
            job = s.get(Job, job_id)
            if job.status in {"cancelling", "cancelled", "interrupted"}:
                if job.status != "interrupted":
                    job.status = "cancelled"
                return
            project = s.get(Project, project_id)
            if project.version != job_data["expected_version"]:
                job.status = "conflict"
                job.data = {**job.data, "errors": [*job.data.get("errors", []), "项目在解析期间发生变化，本批次未覆盖项目；请重新导入。"]}
                return
            # No good files: preserve previous snapshot and settings.
            if not parsed_devices:
                job.status = "failed"
                job.progress = 100
                job.data = {**job.data, "errors": [*job.data.get("errors", []), "本批次没有可用配置，项目保持原状态。"]}
                for source in source_rows:
                    s.add(Source(**source))
                return
            effective, evaluated = apply_overrides(result, project.overrides, settings.get("domains"))
            result["stats"] = {"duration_seconds": round(time.perf_counter() - started, 4), "file_count": total,
                               "device_count": len(result["devices"]), "interface_count": len(result["interfaces"])}
            s.add(Snapshot(id=result["id"], project_id=project_id, data=result, created_at=result["created_at"]))
            for source in source_rows:
                s.add(Source(**source))
            project.snapshot_id = result["id"]
            project.settings = {**settings, "identities": identities}
            project.overrides = evaluated
            project.version += 1
            project.updated_at = now()
            job.status = "completed"
            job.progress = 100
            job.updated_at = now()
            job.data = {**job.data, "snapshot_id": result["id"], "duration_seconds": result["stats"]["duration_seconds"],
                        "conflict_count": sum(o.get("status") == "conflict" for o in evaluated)}
    except Exception as exc:
        with store.session(write=True) as s:
            job = s.get(Job, job_id)
            if job:
                job.status = "failed"
                job.updated_at = now()
                job.data = {**job.data, "errors": [*job.data.get("errors", []), f"导入作业失败 ({type(exc).__name__})。项目未被覆盖。"]}
        raise
    finally:
        # Cancellation and exceptions may occur before the next batch boundary.
        # Never lose the per-file results already produced by this worker.
        flush_progress()
        shutil.rmtree(store.root / "uploads" / job_id, ignore_errors=True)
        store.engine.dispose()
