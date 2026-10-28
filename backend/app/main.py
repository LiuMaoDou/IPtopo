"""IPtopo API and local frontend host. Run with a single Uvicorn worker."""
from __future__ import annotations
from contextlib import asynccontextmanager
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version as package_version
import json
import multiprocessing
import os
import shutil
from pathlib import Path
import re
from threading import RLock
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy import func, select

from .exports import csv_archive, drawio_export, filter_graph, without_source_text
from .inference import project_graph
from .ingest import MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, MAX_UPLOAD_BYTES, MAX_ZIP_DEPTH, ingest_uploads
from .jobs import empty_snapshot, import_worker
from .overrides import apply_overrides
from .schemas import CreateProject, DomainRequest, OverrideRequest, OverrideStatus, VersionRequest, ViewRequest
from .source_io import read_source_text, write_source_text
from .store import Job, Project, Snapshot, Source, Observation, Store, job_dict, now, project_summary, uid
from .routing.model import PortSimulationRequest, SimulationRequest, validate_snapshot_routing
from .routing.simulation import simulate, simulate_ports
from .routing.refresh import apply_bandwidth_assumptions, refresh_huawei_routing
from .lsdb.api import register as register_lsdb
from .lsdb.model import Batch as LSDBBatch
from .ai.api import register as register_ai, facts_revision

ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.4.2"
DEFAULT_DOMAINS = {"domains": [{"id": "default", "name": "默认地址域", "confirmed": True}], "mappings": {}, "interface_mappings": {}, "segment_splits": {}}
ACTIVE_JOBS = {"queued", "running", "cancelling"}
MAX_RESTORE_BYTES = int(os.getenv("IPTOPO_MAX_RESTORE_BYTES", str(256 * 1024 * 1024)))


def render_graph(graph):
    """Keep canvas data small; complete inspectable evidence lives in snapshot."""
    node_keys = {"id", "label", "kind", "vendor", "site", "family", "domain_id", "interface_ids", "prefix", "status"}
    edge_keys = {"id", "link_id", "source", "target", "label", "source_label", "target_label",
                 "interface_a_id", "interface_b_id", "device_a_id", "device_b_id", "relation_type",
                 "origin", "review_state", "families", "prefixes", "segment_ids"}
    return {"nodes": [{k: v for k, v in n.items() if k in node_keys} for n in graph["nodes"]],
            "edges": [{**{k: v for k, v in e.items() if k in edge_keys},
                       "evidence": [{k: v for k, v in item.items() if k in {"rule", "domain_id", "family", "prefix"}}
                                    for item in e.get("evidence", [])], "diagnostics": []} for e in graph["edges"]]}


def create_app(data_dir: str | Path | None = None, worker_mode="process") -> FastAPI:
    store = Store(data_dir or os.environ.get("IPTOPO_DATA_DIR", ROOT / "data"))
    workers = {}
    workers_lock = RLock()
    # Views are independent of facts: a pan/drag must not replay every override.
    # Retain only the latest derived model for at most two active projects.
    derived = OrderedDict()
    derived_lock = RLock()

    @asynccontextmanager
    async def lifespan(app):
        store.recover_jobs()
        yield
        if hasattr(app.state, "ai_shutdown"):
            await app.state.ai_shutdown()
        for process in list(workers.values()):
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
        store.engine.dispose()

    app = FastAPI(title="IPtopo", version=VERSION, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=3)
    app.state.store = store

    def reap_workers():
        with workers_lock:
            dead = [(key, process) for key, process in workers.items() if not process.is_alive()]
            for key, _ in dead:
                workers.pop(key, None)
        if not dead:
            return
        for key, process in dead:
            process.join(timeout=0)
            # Only tracked parser jobs own these staging directories. A hard
            # worker exit bypasses the worker's normal finally cleanup.
            temporary = store.root / "uploads" / key
            if temporary.parent.resolve() == (store.root / "uploads").resolve():
                shutil.rmtree(temporary, ignore_errors=True)
        with store.session(write=True) as s:
            for key, process in dead:
                job = s.get(Job, key)
                if job and job.status in ACTIVE_JOBS:
                    job.status = "interrupted"
                    job.updated_at = now()
                    job.data = {**job.data, "errors": [*job.data.get("errors", []),
                        f"解析进程意外退出（退出码 {process.exitcode}），本批次未提交，可以重新导入。"]}

    @app.middleware("http")
    async def limits(request: Request, call_next):
        await run_in_threadpool(reap_workers)
        length = request.headers.get("content-length")
        if length:
            try:
                limit = MAX_RESTORE_BYTES + 2 * 1024 * 1024 if request.url.path == "/api/projects/restore" else MAX_UPLOAD_BYTES
                if int(length) > limit:
                    return JSONResponse(status_code=413, content={"detail": "请求超过上传大小限制"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Content-Length 无效"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_project(s, project_id):
        project = s.get(Project, project_id)
        if not project:
            raise HTTPException(404, "项目不存在")
        return project

    def active_job(s, project_id):
        return s.scalar(select(Job).where(Job.project_id == project_id, Job.status.in_(ACTIVE_JOBS)))

    def check_version(s, project, expected, allow_import=False):
        if project.version != expected:
            raise HTTPException(409, "项目已被更新，请刷新后重试")
        if not allow_import and active_job(s, project.id):
            raise HTTPException(409, "正在导入配置，请等待完成或取消导入")

    def retained_source(s, project_id, source_id):
        source = s.scalar(select(Source).where(Source.project_id == project_id, Source.source_id == source_id))
        if not source:
            return None
        try:
            return read_source_text(store.root / "sources" / source.storage_key)
        except (OSError, UnicodeError):
            return None

    def derived_model(s, project):
        domains = project.settings.get("domains", DEFAULT_DOMAINS)
        signature = (project.snapshot_id, sha256(json.dumps([project.overrides, domains], sort_keys=True).encode()).hexdigest())
        with derived_lock:
            cached = derived.get(project.id)
            if cached and cached["signature"] == signature:
                derived.move_to_end(project.id)
                return cached
        snapshot = s.get(Snapshot, project.snapshot_id) if project.snapshot_id else None
        raw = snapshot.data if snapshot else empty_snapshot(project.id)
        raw = refresh_huawei_routing(raw, lambda source_id: retained_source(s, project.id, source_id))
        output, evaluated = apply_overrides(raw, project.overrides, domains)
        output = apply_bandwidth_assumptions(output)
        cached = {"signature": signature, "snapshot": output, "overrides": evaluated, "public": None, "graphs": {}}
        with derived_lock:
            derived[project.id] = cached
            derived.move_to_end(project.id)
            while len(derived) > 2:
                derived.popitem(last=False)
        return cached

    def effective(s, project):
        cached = derived_model(s, project)
        return cached["snapshot"], cached["overrides"]

    def snapshot_summaries(s, project_id):
        # Select only metadata; history can contain many large immutable models.
        rows = s.execute(select(Snapshot.id, Snapshot.created_at,
            func.json_array_length(Snapshot.data, "$.devices"), func.json_extract(Snapshot.data, "$.stats"))
            .where(Snapshot.project_id == project_id).order_by(Snapshot.created_at.desc()))
        return [{"id": row[0], "created_at": row[1], "device_count": row[2] or 0,
                 "stats": json.loads(row[3]) if row[3] else {}} for row in rows]

    def job_summaries(s, project_id):
        rows = s.execute(select(Job.id, Job.status, Job.progress, Job.created_at, Job.updated_at,
            *[func.json_extract(Job.data, f"$.{key}") for key in ("file_count", "processed", "errors")])
            .where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(20))
        return [{"id": r[0], "status": r[1], "progress": r[2], "created_at": r[3], "updated_at": r[4],
                 "file_count": r[5] or 0, "processed": r[6] or 0, "errors": json.loads(r[7]) if r[7] else [],
                 "results": []} for r in rows]

    def bump(project):
        project.version += 1
        project.updated_at = now()

    def payload(project_id, mode="device"):
        with store.session() as s:
            project = require_project(s, project_id)
            cached = derived_model(s, project)
            if cached["public"] is None:
                cached["public"] = without_source_text({"snapshot": cached["snapshot"], "overrides": cached["overrides"]})
            snapshot, overrides = cached["public"]["snapshot"], cached["public"]["overrides"]
            if mode not in cached["graphs"]:
                cached["graphs"][mode] = render_graph(project_graph(snapshot, mode))
            # Manually added devices can exist before the first imported snapshot.
            has_snapshot = bool(project.snapshot_id or snapshot.get("devices"))
            return {**project_summary(project), "effective_revision": facts_revision(project), "snapshot": snapshot if has_snapshot else None,
                "device_count": len(snapshot.get("devices", [])), "topology": cached["graphs"][mode],
                "domains": snapshot.get("domain_settings", project.settings.get("domains", DEFAULT_DOMAINS)),
                "overrides": overrides, "views": project.views,
                "snapshots": snapshot_summaries(s, project_id), "jobs": job_summaries(s, project_id)}

    def start_job(project_id, uploads, dialect, strategy, expected_version):
        from .parser import SUPPORTED_DIALECTS
        supported = {d["id"] for d in SUPPORTED_DIALECTS}
        if dialect and dialect not in supported:
            raise HTTPException(422, "未知解析器")
        # Validate project state before doing archive work.
        with store.session() as s:
            project = require_project(s, project_id)
            check_version(s, project, expected_version)
        ingested = ingest_uploads(uploads)
        protect_previous = strategy == "replace" and any(item.get("status") == "failed" for item in ingested.results)
        if protect_previous:
            ingested.results.append({"filename": "本批次", "status": "partial", "parse_status": "partial", "message": "输入读取有失败，已保留未成功替换的旧文件，避免将缺失输入当成删除。", "diagnostics": []})
        job_id = uid("job_")
        prepared = []
        folder = store.root / "uploads" / job_id
        folder.mkdir()
        for item in ingested.files:
            storage_key = uid() + ".txt"
            write_source_text(folder / storage_key, item["content"])
            prepared.append({k: v for k, v in item.items() if k != "content"} | {"storage_key": storage_key})
        with store.session(write=True) as s:
            project = require_project(s, project_id)
            check_version(s, project, expected_version)
            count = len(list(s.scalars(select(Job).where(Job.status.in_(ACTIVE_JOBS)))))
            if count >= 2:
                raise HTTPException(429, "已有两个解析作业运行，请稍后重试")
            job = Job(id=job_id, project_id=project_id, status="queued", progress=0,
                data={"uploads": prepared, "expected_version": expected_version, "dialect": dialect or None,
                      "strategy": strategy, "protect_previous_on_input_failure": protect_previous, "results": ingested.results, "errors": [], "file_count": len(prepared), "total_bytes": ingested.total_bytes})
            s.add(job)
        if worker_mode == "inline":
            import_worker(str(store.root), job_id)
        else:
            process = multiprocessing.get_context("spawn").Process(target=import_worker, args=(str(store.root), job_id), daemon=True)
            try:
                process.start()
                with workers_lock:
                    workers[job_id] = process
            except Exception:
                with store.session(write=True) as s:
                    job = s.get(Job, job_id)
                    job.status = "failed"
                    job.data = {**job.data, "errors": ["无法启动解析进程"]}
                raise HTTPException(503, "无法启动解析进程")
        with store.session() as s:
            return job_dict(s.get(Job, job_id))

    register_lsdb(app, store, require_project, check_version, effective, bump)
    register_ai(app, store, require_project, effective)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": VERSION}

    @app.get("/api/capabilities")
    def capabilities():
        from .parser import SUPPORTED_DIALECTS
        from .lsdb.adapters import VENDORS
        return {"dialects": SUPPORTED_DIALECTS,
                "routing": {"protocols": ["ospf", "isis"], "dialects": [d["id"] for d in SUPPORTED_DIALECTS], "families": [4]},
                "lsdb": {"vendors": [{"id": k, "label": v} for k,v in VENDORS.items()], "protocols": ["ospf", "isis"], "families": [4]},
                "limits": {"files": MAX_FILES, "file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES, "zip_depth": MAX_ZIP_DEPTH},
                "versions": {name: package_version(name) for name in ["ttp", "ttp-templates", "netutils", "N2G", "fastapi"]},
                "batfish": {"enabled": False, "note": "可选增强未启用；首版不依赖 Batfish 服务"}}

    @app.get("/api/projects")
    def projects():
        with store.session() as s:
            rows = s.execute(select(Project, func.json_array_length(Snapshot.data, "$.devices"))
                .outerjoin(Snapshot, Project.snapshot_id == Snapshot.id).order_by(Project.updated_at.desc()))
            return [{**project_summary(p), "device_count": (count or 0) + sum(
                1 for edit in p.overrides if edit.get("operation") == "add_device" and edit.get("status") == "active")}
                for p, count in rows]

    @app.post("/api/projects", status_code=201)
    def create_project(body: CreateProject):
        with store.session(write=True) as s:
            project = Project(id=uid("proj_"), name=body.name, description=body.description, settings={"domains": deepcopy(DEFAULT_DOMAINS), "identities": {}}, overrides=[], views=[])
            s.add(project)
            s.flush()
            result_id = project.id
        return JSONResponse(payload(result_id), status_code=201)

    @app.post("/api/projects/restore", status_code=201)
    async def restore(file: Annotated[UploadFile, File()]):
        raw = await file.read(MAX_RESTORE_BYTES + 1)
        if len(raw) > MAX_RESTORE_BYTES:
            raise HTTPException(413, "项目文件过大")
        try:
            bundle = json.loads(raw)
            validate_bundle(bundle)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(422, f"项目恢复文件无效：{exc}") from None
        new_id = uid("proj_")
        project_data = bundle["project"]
        snapshots = deepcopy(bundle["snapshots"])
        id_map = {snap["id"]: uid("snap_") for snap in snapshots}
        observation_id_map = {b["id"]: uid("lsdb_") for b in bundle.get("lsdb_batches", [])}
        overrides = deepcopy(project_data.get("overrides", []))
        for record in overrides:
            if record.get("base_snapshot_id") in id_map:
                record["base_snapshot_id"] = id_map[record["base_snapshot_id"]]
        with store.session(write=True) as s:
            for snap in snapshots:
                snap["id"] = id_map[snap["id"]]
                snap["project_id"] = new_id
                s.add(Snapshot(id=snap["id"], project_id=new_id, data=snap, created_at=snap.get("created_at", now())))
            for observation in bundle.get("lsdb_batches", []):
                data = deepcopy(observation)
                data["id"] = observation_id_map[data["id"]]
                data["config_snapshot_id"] = id_map.get(data.get("config_snapshot_id"))
                s.add(Observation(id=data["id"], project_id=new_id, data=data))
            project = Project(id=new_id, name=project_data["name"], description=project_data.get("description", ""),
                settings=deepcopy(project_data.get("settings", {"domains": DEFAULT_DOMAINS})), views=deepcopy(project_data.get("views", [])), overrides=overrides,
                snapshot_id=id_map.get(project_data.get("snapshot_id")))
            project.settings = {**project.settings, "lsdb_mappings": {
                observation_id_map[key]: value for key, value in project.settings.get("lsdb_mappings", {}).items() if key in observation_id_map}}
            s.add(project)
            for src_id, item in bundle.get("sources", {}).items():
                key = uid() + ".txt"
                write_source_text(store.root / "sources" / key, item["text"])
                s.add(Source(key=uid(), source_id=src_id, project_id=new_id, filename=item["filename"], storage_key=key, encoding="utf-8"))
        return JSONResponse(payload(new_id), status_code=201)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, mode: Literal["device", "segment"] = "device"):
        return JSONResponse(payload(project_id, mode))

    @app.post("/api/projects/{project_id}/imports", status_code=202)
    async def imports(project_id: str, files: Annotated[list[UploadFile], File()], expected_version: Annotated[int, Form()],
                      dialect: Annotated[str, Form()] = "", strategy: Annotated[Literal["merge", "replace"], Form()] = "merge"):
        uploads, size = [], 0
        if len(files) > MAX_FILES:
            raise HTTPException(413, "文件数超过限制")
        for upload in files:
            content = await upload.read(MAX_TOTAL_BYTES + 1)
            size += len(content)
            if size > MAX_TOTAL_BYTES:
                raise HTTPException(413, "上传总量超过限制")
            uploads.append((upload.filename or "configuration.txt", content))
        return start_job(project_id, uploads, dialect, strategy, expected_version)

    @app.post("/api/projects/{project_id}/demo", status_code=202)
    def demo(project_id: str, kind: Literal["default", "huawei-igp", "multi-vendor-igp"] = "default"):
        directory = ROOT / "samples" / (kind if kind != "default" else "demo")
        paths = sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix in {".cfg", ".conf", ".txt", ".set"})
        if not paths:
            raise HTTPException(503, "示例配置尚未安装")
        with store.session() as s:
            project = require_project(s, project_id)
            expected = project.version
        return start_job(project_id, [(p.name, p.read_bytes()) for p in paths], "", "merge", expected)

    @app.get("/api/imports/{job_id}")
    def get_job(job_id: str):
        with store.session() as s:
            job = s.get(Job, job_id)
            if not job:
                raise HTTPException(404, "作业不存在")
            return job_dict(job)

    @app.post("/api/projects/{project_id}/simulate")
    def simulate_project(project_id: str, body: PortSimulationRequest | SimulationRequest):
        with store.session() as s:
            project = require_project(s, project_id)
            check_version(s, project, body.version)
            snapshot, _ = effective(s, project)
            try:
                result = simulate_ports(snapshot, body) if isinstance(body, PortSimulationRequest) else simulate(snapshot, body)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return {**result, "project_version": project.version}

    @app.post("/api/imports/{job_id}/cancel")
    def cancel_job(job_id: str):
        with store.session(write=True) as s:
            job = s.get(Job, job_id)
            if not job:
                raise HTTPException(404, "作业不存在")
            if job.status in ACTIVE_JOBS:
                job.status = "cancelling"
                job.updated_at = now()
            return job_dict(job)

    @app.get("/api/projects/{project_id}/sources/{source_id}")
    def source(project_id: str, source_id: str):
        with store.session() as s:
            require_project(s, project_id)
            src = s.scalar(select(Source).where(Source.project_id == project_id, Source.source_id == source_id))
            if not src:
                raise HTTPException(404, "此项目未包含原配置。可重新导入，或使用包含原文的 JSON 备份恢复。")
            text = read_source_text(store.root / "sources" / src.storage_key)
            return {"filename": src.filename, "text": text, "line_count": len(text.splitlines())}

    @app.post("/api/projects/{project_id}/infer")
    def infer_project(project_id: str, body: VersionRequest):
        with store.session(write=True) as s:
            project = require_project(s, project_id)
            check_version(s, project, body.version)
            _, evaluated = effective(s, project)
            project.overrides = evaluated
            bump(project)
        return JSONResponse(payload(project_id))

    @app.put("/api/projects/{project_id}/domains")
    def set_domains(project_id: str, body: DomainRequest):
        validate_domains(body.domains)
        with store.session(write=True) as s:
            project = require_project(s, project_id)
            check_version(s, project, body.version)
            project.settings = {**project.settings, "domains": body.domains}
            _, evaluated = effective(s, project)
            project.overrides = evaluated
            bump(project)
        return JSONResponse(payload(project_id))

    @app.post("/api/projects/{project_id}/overrides")
    def add_override(project_id: str, body: OverrideRequest):
        with store.session(write=True) as s:
            project = require_project(s, project_id)
            check_version(s, project, body.version)
            record = {"id": uid("ovr_"), "operation": body.operation, "target": body.target, "value": body.value,
                      "reason": body.reason, "status": "active", "base_snapshot_id": project.snapshot_id, "created_at": now()}
            if body.baseline is not None:
                record["baseline"] = body.baseline
            project.overrides = [*project.overrides, record]
            _, evaluated = effective(s, project)
            project.overrides = evaluated
            bump(project)
        return JSONResponse(payload(project_id))

    @app.patch("/api/projects/{project_id}/overrides/{override_id}")
    def change_override(project_id: str, override_id: str, body: OverrideStatus):
        with store.session(write=True) as s:
            project = require_project(s, project_id)
            check_version(s, project, body.version)
            records = deepcopy(project.overrides)
            record = next((r for r in records if r["id"] == override_id), None)
            if not record:
                raise HTTPException(404, "修正记录不存在")
            record["status"] = body.status
            project.overrides = records
            _, evaluated = effective(s, project)
            project.overrides = evaluated
            bump(project)
        return JSONResponse(payload(project_id))

    @app.put("/api/projects/{project_id}/views/{view_id}")
    def save_view(project_id: str, view_id: str, body: ViewRequest, summary: bool = False):
        if body.view.id != view_id:
            raise HTTPException(422, "视图 ID 不匹配")
        with store.session(write=True) as s:
            project = require_project(s, project_id)
            check_version(s, project, body.version)
            view = body.view.model_dump()
            project.views = [v for v in project.views if v["id"] != view_id] + [view]
            bump(project)
            result = {"id": project.id, "version": project.version, "updated_at": project.updated_at, "views": project.views}
        return JSONResponse(result if summary else payload(project_id))

    @app.get("/api/projects/{project_id}/snapshots")
    def list_snapshots(project_id: str):
        with store.session() as s:
            require_project(s, project_id)
            return snapshot_summaries(s, project_id)

    @app.get("/api/projects/{project_id}/snapshots/{snapshot_id}")
    def get_snapshot(project_id: str, snapshot_id: str, mode: Literal["device", "segment"] = "device"):
        with store.session() as s:
            project = require_project(s, project_id)
            snapshot = s.get(Snapshot, snapshot_id)
            if not snapshot or snapshot.project_id != project_id:
                raise HTTPException(404, "快照不存在")
            data, _ = apply_overrides(deepcopy(snapshot.data), [], snapshot.data.get("domain_settings", DEFAULT_DOMAINS))
            return JSONResponse(without_source_text({"snapshot": data, "topology": render_graph(project_graph(data, mode))}))

    @app.get("/api/projects/{project_id}/exports/{format}")
    def export(project_id: str, format: Literal["json", "csv", "drawio"], view_id: str | None = None,
               include_sources: bool = False, mode: Literal["device", "segment"] = "device"):
        with store.session() as s:
            project = require_project(s, project_id)
            snapshot, _ = effective(s, project)
            view = next((v for v in project.views if v["id"] == view_id), None)
            if view_id and not view:
                raise HTTPException(404, "视图不存在")
            filename = project.name
            if format == "json":
                bundle = {"format": "iptopo.project", "schema_version": 1, "exported_at": now(),
                    "project": {**project_summary(project), "settings": project.settings, "overrides": project.overrides, "views": project.views},
                    "snapshots": [refresh_huawei_routing(x.data, lambda source_id: retained_source(s, project_id, source_id))
                                  for x in s.scalars(select(Snapshot).where(Snapshot.project_id == project_id).order_by(Snapshot.created_at))],
                    "lsdb_batches": [x.data for x in s.scalars(select(Observation).where(Observation.project_id == project_id).order_by(Observation.created_at))],
                    "sources": {}, "include_sources": include_sources}
                if include_sources:
                    bundle["sources"] = {src.source_id: {"filename": src.filename, "text": read_source_text(store.root / "sources" / src.storage_key)}
                        for src in s.scalars(select(Source).where(Source.project_id == project_id))}
                else:
                    bundle = without_source_text(bundle)
                content = json.dumps(bundle, ensure_ascii=False, indent=2).encode()
                if len(content) > MAX_RESTORE_BYTES:
                    raise HTTPException(413, "备份超过当前恢复限额。请调整 IPTOPO_MAX_RESTORE_BYTES 后重试，或备份完整数据目录。")
                mime, extension = "application/json", ".iptopo.json"
            else:
                graph = filter_graph(snapshot, view, mode)
                if format == "csv":
                    content, mime, extension = csv_archive(snapshot, graph), "application/zip", ".csv.zip"
                else:
                    content, mime, extension = drawio_export(graph, view, project.name), "application/xml", ".drawio"
            return Response(content, media_type=mime, headers={"Content-Disposition": f"attachment; filename=iptopo{extension}; filename*=UTF-8''{quote(filename + extension)}"})

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API 不存在")
        if not (dist / "index.html").is_file():
            return JSONResponse({"detail": "前端尚未构建，请在 frontend 目录执行 npm ci && npm run build", "docs": "/docs"}, status_code=503)
        if path in {"favicon.svg", "favicon.ico", "robots.txt"} and (dist / path).is_file():
            return FileResponse(dist / path)
        return FileResponse(dist / "index.html")

    return app


def validate_domains(settings):
    if not isinstance(settings.get("domains"), list) or len(settings["domains"]) > 10000:
        raise HTTPException(422, "地址域列表无效")
    ids = []
    for domain in settings["domains"]:
        if not isinstance(domain, dict) or not isinstance(domain.get("id"), str) or not isinstance(domain.get("name"), str) or not isinstance(domain.get("confirmed", False), bool):
            raise HTTPException(422, "地址域字段无效")
        ids.append(domain["id"])
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "地址域 ID 重复")
    splits = settings.get("segment_splits", {})
    if not isinstance(splits, dict) or any(not isinstance(v, dict) or any(not isinstance(x, str) for x in v.values()) for v in splits.values()):
        raise HTTPException(422, "网段拆分字段无效")
    for key in ("mappings", "interface_mappings"):
        if not isinstance(settings.get(key, {}), dict) or any(v not in ids for v in settings.get(key, {}).values()):
            raise HTTPException(422, "映射引用了不存在的地址域")


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.get("format") != "iptopo.project" or bundle.get("schema_version") != 1:
        raise ValueError("仅支持 schema_version=1 的 IPtopo 项目")
    project = bundle["project"]
    CreateProject.model_validate(project)
    snapshots = bundle["snapshots"]
    if not isinstance(snapshots, list):
        raise ValueError("快照列表无效")
    if len(snapshots) > 10000:
        raise ValueError("快照过多")
    observations = bundle.get("lsdb_batches", [])
    if not isinstance(observations, list) or len(observations) > 10000:
        raise ValueError("LSDB 批次列表无效")
    observation_ids = set()
    for item in observations:
        observation = LSDBBatch.model_validate(item)
        if observation.id in observation_ids:
            raise ValueError("LSDB 批次 ID 重复")
        observation_ids.add(observation.id)
    lsdb_mappings = project.get("settings", {}).get("lsdb_mappings", {})
    if not isinstance(lsdb_mappings, dict) or any(key not in observation_ids or not isinstance(value, dict)
            or any(not isinstance(a, str) or not isinstance(b, str) for a, b in value.items()) for key, value in lsdb_mappings.items()):
        raise ValueError("LSDB 身份关联数据无效")
    snapshot_ids = set()
    for snap in snapshots:
        if snap["id"] in snapshot_ids:
            raise ValueError("快照 ID 重复")
        snapshot_ids.add(snap["id"])
        for kind in ("devices", "interfaces", "contexts", "source_files", "diagnostics"):
            if not isinstance(snap.get(kind), list):
                raise ValueError(f"{kind} 必须为列表")
            ids = [x["id"] for x in snap[kind] if "id" in x]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{kind} ID 重复")
        device_ids = {d["id"] for d in snap["devices"]}
        validate_snapshot_routing(snap)
        for interface in snap["interfaces"]:
            if interface["device_id"] not in device_ids or not isinstance(interface.get("addresses", []), list):
                raise ValueError("接口归属或地址结构无效")
        # Exercise actual inference before committing restored state.
        apply_overrides(deepcopy(snap), [], project.get("settings", {}).get("domains", DEFAULT_DOMAINS))
    for observation in observations:
        if observation.get("config_snapshot_id") and observation["config_snapshot_id"] not in snapshot_ids:
            raise ValueError("LSDB 引用的配置快照不存在")
    if project.get("snapshot_id") and project["snapshot_id"] not in snapshot_ids:
        raise ValueError("当前快照不存在")
    settings = project.get("settings", {})
    if not isinstance(settings, dict) or not isinstance(settings.get("identities", {}), dict):
        raise ValueError("项目设置无效")
    validate_domains(settings.get("domains", DEFAULT_DOMAINS))
    if not isinstance(project.get("overrides", []), list):
        raise ValueError("修正记录无效")
    override_ids = set()
    for record in project.get("overrides", []):
        if not isinstance(record, dict):
            raise ValueError("修正记录必须为对象")
        OverrideRequest.model_validate({**record, "version": 1})
        if not isinstance(record.get("id"), str) or record["id"] in override_ids or record.get("status") not in {"active", "conflict", "undone"}:
            raise ValueError("修正记录 ID 或状态无效")
        override_ids.add(record["id"])
    current = next((snap for snap in snapshots if snap["id"] == project.get("snapshot_id")), empty_snapshot("restore_validation"))
    apply_overrides(deepcopy(current), project.get("overrides", []), settings.get("domains", DEFAULT_DOMAINS))
    for view in project.get("views", []):
        ViewRequest.model_validate({"version": 1, "view": view})
    if not isinstance(bundle.get("sources", {}), dict):
        raise ValueError("原配置字段无效")
    for item in bundle.get("sources", {}).values():
        if not isinstance(item.get("text"), str) or not isinstance(item.get("filename"), str):
            raise ValueError("原配置记录无效")


app = create_app()
