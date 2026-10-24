#!/usr/bin/env python3
"""Measure real HTTP import/query/inference/export using isolated Uvicorn workers.

Results are local synthetic measurements, not real-device compatibility or
browser rendering promises. RSS is sampled process-tree RSS (shared pages can
be counted twice); every size starts a fresh server and temporary database.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.generate_scale_samples import create_archive

ROOT = Path(__file__).resolve().parents[1]


class MemorySampler:
    def __init__(self, pid):
        self.pid = pid
        self.stop_event = threading.Event()
        self.server_peak_kib = 0
        self.tree_peak_kib = 0
        self.max_descendants = 0
        self.samples = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop_event.is_set():
            output = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], check=True, capture_output=True, text=True).stdout
            entries = {int(row[0]): (int(row[1]), int(row[2])) for line in output.splitlines() if len(row := line.split()) == 3}
            selected = {self.pid}
            while True:
                children = {pid for pid, (parent, _) in entries.items() if parent in selected}
                if children <= selected:
                    break
                selected |= children
            self.server_peak_kib = max(self.server_peak_kib, entries.get(self.pid, (0, 0))[1])
            self.tree_peak_kib = max(self.tree_peak_kib, sum(entries.get(pid, (0, 0))[1] for pid in selected))
            self.max_descendants = max(self.max_descendants, len(selected) - 1)
            self.samples += 1
            self.stop_event.wait(0.05)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join(timeout=5)

    def result(self):
        return {"server_peak_rss_mib": round(self.server_peak_kib / 1024, 2), "process_tree_peak_rss_mib": round(self.tree_peak_kib / 1024, 2),
                "memory_samples": self.samples, "max_worker_descendants": self.max_descendants}


@contextmanager
def server(folder):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    env = {**os.environ, "IPTOPO_DATA_DIR": str(folder / "data")}
    for key in ("IPTOPO_MAX_FILES", "IPTOPO_MAX_FILE_BYTES", "IPTOPO_MAX_TOTAL_BYTES", "IPTOPO_MAX_ZIP_DEPTH"):
        env.pop(key, None)
    with (folder / "server.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app", "--fd", str(listener.fileno()), "--log-level", "warning"], cwd=ROOT, env=env, pass_fds=(listener.fileno(),), start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
        listener.close()
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180, trust_env=False) as client:
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError((folder / "server.log").read_text())
                    try:
                        if client.get("/api/health", timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("Benchmark server readiness timeout")
                    time.sleep(0.025)
                yield client, process.pid
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def timed_request(client, method, path, **kwargs):
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    stats = {"seconds": round(time.perf_counter() - started, 4), "decoded_bytes": len(response.content),
             "wire_content_length": int(response.headers["content-length"]) if response.headers.get("content-length") else None,
             "content_encoding": response.headers.get("content-encoding", "identity")}
    return response, stats


def benchmark(count):
    with tempfile.TemporaryDirectory(prefix=f"iptopo-api-{count}-") as directory:
        folder = Path(directory)
        archive = folder / f"scale-{count}.zip"
        expected = create_archive(count, archive)
        with server(folder) as (client, pid), MemorySampler(pid) as memory:
            project = client.post("/api/projects", json={"name": f"Synthetic {count}"}).json()
            started = time.perf_counter()
            response, upload = timed_request(client, "POST", f"/api/projects/{project['id']}/imports", files={"files": (archive.name, archive.read_bytes(), "application/zip")}, data={"expected_version": project["version"]})
            job_id = response.json()["id"]
            deadline = time.monotonic() + 240
            while True:
                job = client.get(f"/api/imports/{job_id}").json()
                if job["status"] in {"completed", "failed", "conflict", "cancelled", "interrupted"}:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Import timeout for {count} devices")
                time.sleep(0.1)
            import_seconds = time.perf_counter() - started
            assert job["status"] == "completed", job
            response, cold_get = timed_request(client, "GET", f"/api/projects/{project['id']}")
            project = response.json()
            assert len(project["snapshot"]["devices"]) == count
            assert len(project["snapshot"]["interfaces"]) == expected["interfaces"]
            assert len(project["snapshot"]["links"]) == expected["logical_links"]
            _, warm_get = timed_request(client, "GET", f"/api/projects/{project['id']}")
            positions = {node["id"]: {"x": float(index % 40 * 160), "y": float(index // 40 * 100)} for index, node in enumerate(project["topology"]["nodes"])}
            view = {"id": "scale-view", "name": "Scale validation", "positions": positions, "pinned": [], "layout": "preset"}
            response, view_save = timed_request(client, "PUT", f"/api/projects/{project['id']}/views/scale-view?summary=true", json={"version": project["version"], "view": view})
            assert "snapshot" not in response.json()
            project["version"] = response.json()["version"]
            _, after_view_get = timed_request(client, "GET", f"/api/projects/{project['id']}")
            response, inference = timed_request(client, "POST", f"/api/projects/{project['id']}/infer", json={"version": project["version"]})
            assert len(response.json()["snapshot"]["links"]) == expected["logical_links"]
            exports = {}
            for fmt in ("json", "csv", "drawio"):
                response, stats = timed_request(client, "GET", f"/api/projects/{project['id']}/exports/{fmt}")
                if fmt == "drawio":
                    xml = ET.fromstring(response.content)
                    assert len(xml.findall(".//object[@kind='device']")) == count
                    assert len(xml.findall(".//mxCell[@edge='1']")) == expected["logical_links"]
                exports[fmt] = stats
            result = {k: v for k, v in expected.items() if k != "output"}
            result.update(import_wall_seconds=round(import_seconds, 4), import_worker_seconds=job.get("duration_seconds"),
                          upload_request=upload, cold_get=cold_get, warm_get=warm_get, view_save=view_save, after_view_get=after_view_get, infer=inference, exports=exports)
        result.update(memory.result())
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", nargs="+", type=int, default=[100, 1000, 2000])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    hardware = subprocess.run(["sysctl", "-n", "hw.model", "machdep.cpu.brand_string", "hw.memsize"], capture_output=True, text=True).stdout.strip().splitlines() if platform.system() == "Darwin" else []
    report = {"hardware": hardware, "cpu_count": os.cpu_count(), "method": "real HTTP; Uvicorn and actual spawned import workers; isolated temporary databases; 50ms process-tree RSS sampling", "python": platform.python_version(), "system": platform.system(), "machine": platform.machine(), "results": []}
    for count in args.devices:
        result = benchmark(count)
        report["results"].append(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
