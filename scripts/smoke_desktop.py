"""Exercise the shipped executable, real parser processes and persisted data."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import httpx


def wait_for(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError("Timed out waiting for desktop application")


def visible_launcher(pid):
    if sys.platform != "win32":
        return True
    matches = []
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if owner.value == pid and title.value == "IPtopo" and user32.IsWindowVisible(hwnd):
            matches.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return bool(matches)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    command = [str(args.exe.resolve())] if args.exe else [sys.executable, str(repo / "desktop.py")]
    with tempfile.TemporaryDirectory(prefix="iptopo-smoke-") as temp:
        root = Path(temp).resolve() / "中文路径 with spaces"
        root.mkdir()
        data = root / "data"
        ready = root / "ready.json"
        stop = root / "stop"
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "IPTOPO_DATA_DIR"):
            env.pop(key, None)
        if args.exe and sys.platform == "win32":
            # No Python or Node executable can be found by the standalone app.
            windows_root = os.environ["SYSTEMROOT"]
            env["PATH"] = os.pathsep.join([windows_root, str(Path(windows_root) / "System32")])
        launch = command + ["--no-browser", "--data-dir", str(data), "--ready-file", str(ready), "--stop-file", str(stop)]
        if not args.exe:
            launch.append("--headless")
        project_id = None
        juniper_project_id = None
        aggregation_project_id = None
        try:
            for cycle in range(2):
                stop.unlink(missing_ok=True)
                process = subprocess.Popen(launch, cwd=root, env=env)
                try:
                    def started():
                        assert process.poll() is None, f"Launcher exited: {process.returncode}"
                        try:
                            return json.loads(ready.read_text(encoding="utf-8"))
                        except (FileNotFoundError, json.JSONDecodeError):
                            return None
                    info = wait_for(started)
                    assert Path(info["data_dir"]) == data
                    if args.exe:
                        assert visible_launcher(info["pid"]), "Launcher window is missing"
                    with httpx.Client(base_url=info["url"], trust_env=False, timeout=30) as client:
                        assert client.get("api/health").json()["status"] == "ok"
                        assert client.get("api/capabilities").json()["versions"]["ttp"]
                        page = client.get("")
                        assert page.status_code == 200 and "IPtopo" in page.text
                        for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text):
                            assert client.get(asset).status_code == 200
                        if cycle == 0:
                            response = client.post("api/projects", json={"name": "Windows packaging smoke"})
                            response.raise_for_status()
                            project_id = response.json()["id"]
                            response = client.post(f"api/projects/{project_id}/demo")
                            response.raise_for_status()
                            job_id = response.json()["id"]
                            def imported():
                                job = client.get(f"api/imports/{job_id}").json()
                                assert job["status"] not in {"failed", "interrupted", "cancelled"}, job
                                return job if job["status"] == "completed" else None
                            wait_for(imported)
                            juniper_project = client.post("api/projects", json={"name": "Junos subtree regression"}).json()
                            juniper_project_id = juniper_project["id"]
                            files = [("files", (f"junos-{i}.cfg", f"user@edge{i}> show configuration interfaces\nge-0/0/0 {{ unit 0 {{ family inet {{ address 192.0.2.{i}/31 {{}} }} family inet6 {{ address 2001:db8::{i}/127; }} }} }}".encode(), "application/octet-stream")) for i in range(2)]
                            response = client.post(f"api/projects/{juniper_project_id}/imports", files=files, data={"expected_version": str(juniper_project["version"])})
                            response.raise_for_status()
                            job_id = response.json()["id"]
                            wait_for(imported)
                            aggregation_project = client.post("api/projects", json={"name": "Four-vendor LACP regression"}).json()
                            aggregation_project_id = aggregation_project["id"]
                            files = [("files", (path.name, path.read_bytes(), "application/octet-stream")) for path in sorted((repo / "samples" / "fixtures").glob("lag-*.cfg"))]
                            assert len(files) == 4
                            response = client.post(f"api/projects/{aggregation_project_id}/imports", files=files, data={"expected_version": str(aggregation_project["version"])})
                            response.raise_for_status()
                            job_id = response.json()["id"]
                            wait_for(imported)
                            duplicate = subprocess.run(command + ["--headless", "--no-browser", "--data-dir", str(data)], env=env, cwd=root, timeout=60)
                            assert duplicate.returncode == 1, "Duplicate instance was not rejected"
                            assert client.get("api/health").status_code == 200
                        project = client.get(f"api/projects/{project_id}").json()
                        assert project["device_count"] == 8, project
                        assert len(project["topology"]["edges"]) > 0
                        juniper = client.get(f"api/projects/{juniper_project_id}").json()
                        assert juniper["device_count"] == 2
                        assert len(juniper["snapshot"]["links"]) == 1
                        assert set(juniper["snapshot"]["links"][0]["families"]) == {4, 6}
                        aggregation = client.get(f"api/projects/{aggregation_project_id}").json()["snapshot"]
                        assert {d["vendor"] for d in aggregation["devices"]} == {"Huawei", "Cisco", "Juniper", "Nokia"}
                        assert len(aggregation["links"]) == 2
                        assert all(set(link["families"]) == {4, 6} for link in aggregation["links"])
                        lags = [i for i in aggregation["interfaces"] if i["kind"] == "aggregate"]
                        assert len(lags) == 4
                        assert all(i["aggregation"]["protocol"] == "lacp" and len(i["aggregate_members"]) == 2 for i in lags)
                        for format in ("json", "csv", "drawio"):
                            exported = client.get(f"api/projects/{project_id}/exports/{format}")
                            assert exported.status_code == 200, exported.text
                            if format == "drawio":
                                assert ET.fromstring(exported.content).find(".//root") is not None
                    stop.touch()
                    assert process.wait(timeout=30) == 0
                    assert (data / "iptopo.sqlite").is_file()
                    assert not ready.exists()
                finally:
                    if process.poll() is None:
                        stop.touch()
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            print("PASS: startup, bundled assets, metadata, eight-device multiprocess import, Junos subtree dual-stack inference, four-vendor LACP ownership, exports, single instance, shutdown and restart persistence")
        except Exception:
            log = data / "desktop.log"
            if log.exists():
                print(log.read_text(encoding="utf-8"), file=sys.stderr)
            raise


if __name__ == "__main__":
    main()
