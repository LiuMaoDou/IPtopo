"""Exercise the shipped executable, real parser processes and persisted data."""
import argparse
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

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


def checked_json(response):
    response.raise_for_status()
    return response.json()


def wait_for_import(client, job_id):
    def imported():
        job = checked_json(client.get(f"api/imports/{job_id}"))
        assert job["status"] not in {"failed", "interrupted", "cancelled", "conflict"}, job
        return job if job["status"] == "completed" else None
    return wait_for(imported)


def check_frontend_assets(client):
    """Check the files served by the app, including chunks bundled into the EXE."""
    page = client.get("")
    assert page.status_code == 200 and "IPtopo" in page.text
    styles, scripts = [], []
    for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text):
        response = client.get(asset)
        assert response.status_code == 200, f"Bundled asset unavailable: {asset}"
        if asset.endswith(".css"):
            styles.append(response.text)
        elif asset.endswith(".js"):
            scripts.append(response.text)
    assert styles and scripts, "Production CSS or JavaScript is missing"
    css = "\n".join(styles)
    assert re.search(r"\.app-shell\s*\{[^}]*min-width\s*:\s*1280px", css), "PC minimum workspace width is missing"
    assert ".mobile-nav" not in css, "Obsolete phone navigation was bundled"
    assert not re.search(r"@media[^{}]*max-width\s*:\s*(?:760|1000)px", css), "Phone/tablet panel switching CSS was bundled"
    javascript = "\n".join(scripts)
    assert "mobile-nav" not in javascript, "Obsolete phone navigation JavaScript was bundled"
    for label in ("设为仿真源", "设为仿真目的", "已忽略的配置语义"):
        assert label in javascript, f"Updated path simulation UI was not bundled: {label}"
    # Vite emits these as lazy chunks; the HTML alone does not reference them.
    for prefix in ("ChatPanel", "layout.worker"):
        names = set(re.findall(re.escape(prefix)+r"-[A-Za-z0-9_-]+\.js", javascript))
        assert names, f"The {prefix} production chunk is not referenced"
        for name in names:
            response = client.get("assets/"+name)
            assert response.status_code == 200, f"Lazy bundled asset unavailable: {name}"
            assert "javascript" in response.headers.get("content-type", ""), f"Lazy asset is not JavaScript: {name}"
            assert len(response.content) > 100


def assert_three_way_ecmp(result, description):
    observed = {key: result.get(key) for key in ("status", "total_cost", "path_count")}
    assert observed == {"status": "ok", "total_cost": 30, "path_count": "3"}, f"{description}: {observed}"
    assert len(result["paths"]) == 3, description
    shares = sorted(round(float(path["share_percent"]), 6) for path in result["paths"])
    assert shares == [25, 25, 50], f"{description}: unexpected ECMP shares {shares}"
    assert result["hops"], f"{description}: no forwarding hops"


def configuration_path_request(project, protocol):
    """Discover real IDs from the packaged app's imported facts, never fixtures."""
    snapshot = project["snapshot"]
    devices = {device["hostname"]: device["id"] for device in snapshot["devices"]}
    instances = {item["id"]: item for item in snapshot["routing"]["instances"] if item["protocol"] == protocol}
    source = next(item for item in instances.values() if item["device_id"] == devices["IGP-S"])
    scope = "0.0.0.0" if protocol == "ospf" else "2"
    bindings = {item["id"] for item in snapshot["routing"]["bindings"] if item["instance_id"] in instances and item["scope"] == scope}
    interfaces = {item["id"]: item for item in snapshot["interfaces"]}
    destination = next(item for item in snapshot["routing"]["advertisements"]
                       if item["device_id"] == devices["IGP-D"] and item["binding_id"] in bindings
                       and item["prefix"].endswith("/32") and interfaces[item["interface_id"]]["kind"] == "loopback")
    return {"protocol": protocol, "scope": scope, "source_instance_id": source["id"],
            "destination_advertisement_id": destination["id"]}


def check_renamed_zip_merge(client, project_id, repo):
    """Reimport the same members under new ZIP names through the real parser."""
    project = checked_json(client.get(f"api/projects/{project_id}"))
    original_devices = {item["hostname"]: item["id"] for item in project["snapshot"]["devices"]}
    assert len(original_devices) == 6
    samples = sorted((repo / "samples" / "multi-vendor-igp").glob("*.cfg"))
    assert len(samples) == 6, "The six-device mixed-vendor routing fixtures are missing"
    previous_names = set()
    for archive_name, add_ignored in (("routing-capture-before.zip", False), ("routing-capture-after.zip", True)):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for sample in samples:
                content = sample.read_bytes()
                if add_ignored and sample.name == "01-S-cisco-ios.cfg":
                    content, count = re.subn(rb"(?m)^(router ospf 1)(\r\n|\r|\n)",
                                            rb"\1\2 timers throttle spf 50 100 1000\2", content, count=1)
                    assert count == 1, "The Cisco OSPF fixture no longer has the expected process"
                archive.writestr(sample.name, content)
        imported = checked_json(client.post(f"api/projects/{project_id}/imports",
            files=[("files", (archive_name, buffer.getvalue(), "application/zip"))],
            data={"strategy": "merge", "expected_version": str(project["version"])}))
        wait_for_import(client, imported["id"])
        project = checked_json(client.get(f"api/projects/{project_id}"))
        snapshot = project["snapshot"]
        assert project["device_count"] == 6, "Renaming a ZIP duplicated its devices"
        assert {item["hostname"]: item["id"] for item in snapshot["devices"]} == original_devices, "A ZIP merge changed device IDs"
        current_names = {item["filename"] for item in snapshot["source_files"]}
        assert len(snapshot["source_files"]) == 6
        assert current_names == {f"{archive_name}/{sample.name}" for sample in samples}, "Old current source filenames survived a ZIP merge"
        assert not current_names & previous_names
        previous_names = current_names
        instances = snapshot["routing"]["instances"]
        assert len(instances) == len({item["id"] for item in instances}) == 12, "ZIP reimport duplicated routing instances"
        assert len({(item["device_id"], item["protocol"], item["routing_context_id"], item["local_name"]) for item in instances}) == 12
    result = checked_json(client.post(f"api/projects/{project_id}/simulate",
        json={"version": project["version"], **configuration_path_request(project, "ospf")}))
    assert_three_way_ecmp(result, "Renamed ZIP with ignored Cisco OSPF statement")
    assert any(item.get("ignored") is True and item.get("blocking") is False for item in result["diagnostics"]), "Unsupported semantics did not produce a nonblocking ignored diagnostic"


def check_routing_and_lsdb(client, project_id, batches):
    project = checked_json(client.get(f"api/projects/{project_id}"))
    assert project["device_count"] == 6
    assert {device["vendor"] for device in project["snapshot"]["devices"]} == {"Huawei", "Cisco", "Juniper", "Nokia"}
    assert len(project["snapshot"]["routing"]["instances"]) == 12
    for protocol in ("ospf", "isis"):
        body = {"version": project["version"], **configuration_path_request(project, protocol)}
        result = checked_json(client.post(f"api/projects/{project_id}/simulate", json=body))
        assert_three_way_ecmp(result, f"{protocol.upper()} configuration path")
    available = checked_json(client.get(f"api/projects/{project_id}/lsdb"))["batches"]
    assert {item["id"] for item in available} == {item["id"] for item in batches}, "Native LSDB batches were not persisted"
    for batch in batches:
        path = f"api/projects/{project_id}/lsdb/{batch['id']}"
        stored = checked_json(client.get(path))
        assert stored["vendor"] == batch["vendor"] and stored["protocol"] == batch["protocol"]
        assert stored["records"] and stored["source_files"]
        body = {"version": project["version"], "mode": "fused"}
        inventory = checked_json(client.post(path+"/analyze", json=body))
        source_id = "10.255.0.1" if batch["protocol"] == "ospf" else "0000.0000.0001.00"
        source = next(item for item in inventory["nodes"] if item["protocol_id"] == source_id)
        destination = next(item for item in inventory["destinations"] if item["prefix"] == "10.255.0.6/32")
        result = checked_json(client.post(path+"/analyze", json={**body, "source": source["id"], "destination": destination["id"]}))
        description = f"{batch['vendor']} {batch['protocol'].upper()} LSDB"
        assert_three_way_ecmp(result["simulation"], description)
        assert result["config_simulation"] is not None, f"{description}: configuration identity fusion is missing"
        assert_three_way_ecmp(result["config_simulation"], description+" fused configuration")
    return project


def check_readonly_ai(client, project, previous_token=None):
    """Exercise the packaged AI tool surface without a CLI or model request."""
    project_id = project["id"]
    base = f"api/projects/{project_id}"
    before = checked_json(client.get(base+"/chat/context"))
    if previous_token:
        response = client.post("api/ai/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                               headers={"Authorization": "Bearer "+previous_token})
        assert response.status_code == 401, "A pre-restart MCP token remained valid"
    grant = checked_json(client.post(base+"/chat/mcp-token", json={"ttl_minutes": 1}))
    assert grant["context"]["revision"] == before["revision"]
    assert set(grant["config"]) >= {"codex", "opencode", "opencode_v2"}
    assert grant["token"] not in json.dumps(grant["config"]), "A connection example embedded the secret instead of an environment reference"
    headers = {"Authorization": "Bearer "+grant["token"], "MCP-Protocol-Version": "2025-11-25",
               "Accept": "application/json, text/event-stream"}
    request_id = 0

    def rpc(method, params):
        nonlocal request_id
        request_id += 1
        response = client.post("api/ai/mcp", headers=headers, json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        value = checked_json(response)
        assert value["id"] == request_id
        return value

    initialized = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "iptopo-exe-smoke", "version": "1"}})
    assert initialized["result"]["serverInfo"]["name"] == "iptopo-readonly"
    notification = client.post("api/ai/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    assert notification.status_code == 202
    catalog = rpc("tools/list", {})["result"]["tools"]
    names = {tool["name"] for tool in catalog}
    assert names >= {"network_summary", "search_devices", "device_details", "query_topology", "list_path_targets", "config_path", "lsdb_path", "compare", "get_evidence"}
    assert all(tool["annotations"]["readOnlyHint"] and not tool["annotations"]["destructiveHint"] for tool in catalog)

    def call(name, arguments):
        result = rpc("tools/call", {"name": name, "arguments": arguments})["result"]
        assert not result["isError"], f"Read-only tool failed: {name}"
        output = result["structuredContent"]
        assert output == json.loads(result["content"][0]["text"])
        assert output["context"]["project_id"] == project_id
        assert output["context"]["revision"] == before["revision"]
        return output

    summary = call("network_summary", {})
    assert summary["data"]["device_count"] == 6 and len(summary["data"]["vendors"]) == 4
    found = call("search_devices", {"query": "IGP-S"})["data"]["devices"]
    assert len(found) == 1 and found[0]["hostname"] == "IGP-S"
    details = call("device_details", {"device_id": found[0]["id"]})
    assert details["data"]["interfaces"] and details["evidence"]
    topology = call("query_topology", {"device_id": found[0]["id"], "hops": 1})
    assert len(topology["data"]["nodes"]) >= 3 and topology["data"]["edges"]
    targets = call("list_path_targets", {"protocol": "ospf", "scope": "0.0.0.0"})
    assert targets["data"]["instances"] and targets["data"]["advertisements"]
    computed = call("config_path", configuration_path_request(project, "ospf"))
    assert_three_way_ecmp(computed["data"], "MCP configuration path")
    assert computed["cards"][0]["kind"] == "path" and computed["cards"][0].get("highlight")
    ref = details["evidence"][0]
    evidence = call("get_evidence", {"source_file_id": ref["source_file_id"], "line": ref["line"], "end_line": ref["line"]})
    assert evidence["data"]["redacted"] is True and len(evidence["data"]["lines"]) == 1
    refused = rpc("tools/call", {"name": "delete_project", "arguments": {}})
    assert refused["error"]["code"] == -32602, "An undeclared write tool was accepted"
    unauthorized = client.post("api/ai/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert unauthorized.status_code == 401
    after = checked_json(client.get(base+"/chat/context"))
    assert after == before, "Read-only AI queries changed project facts"
    return grant["token"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--expected-version", help="Assert the packaged API reports this release version")
    parser.add_argument("--codex-exe", type=Path, help="Probe this native Codex executable without signing in or requesting a model")
    args = parser.parse_args()
    if args.codex_exe:
        args.codex_exe = args.codex_exe.resolve(strict=True)
        assert args.codex_exe.is_file(), "--codex-exe must point to the native executable"
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
        routing_project_id = None
        native_batches = []
        chat_session_id = None
        saved_ai_settings = None
        previous_mcp_token = None
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
                        health = checked_json(client.get("api/health"))
                        assert health["status"] == "ok"
                        if args.expected_version:
                            assert health["version"] == args.expected_version, "Packaged version does not match the release"
                        assert client.get("api/capabilities").json()["versions"]["ttp"]
                        check_frontend_assets(client)
                        if cycle == 0:
                            response = client.post("api/projects", json={"name": "Windows packaging smoke"})
                            response.raise_for_status()
                            project_id = response.json()["id"]
                            response = client.post(f"api/projects/{project_id}/demo")
                            response.raise_for_status()
                            job_id = response.json()["id"]
                            wait_for_import(client, job_id)
                            juniper_project = client.post("api/projects", json={"name": "Junos subtree regression"}).json()
                            juniper_project_id = juniper_project["id"]
                            files = [("files", (f"junos-{i}.cfg", f"user@edge{i}> show configuration interfaces\nge-0/0/0 {{ unit 0 {{ family inet {{ address 192.0.2.{i}/31 {{}} }} family inet6 {{ address 2001:db8::{i}/127; }} }} }}".encode(), "application/octet-stream")) for i in range(2)]
                            response = client.post(f"api/projects/{juniper_project_id}/imports", files=files, data={"expected_version": str(juniper_project["version"])})
                            response.raise_for_status()
                            job_id = response.json()["id"]
                            wait_for_import(client, job_id)
                            aggregation_project = client.post("api/projects", json={"name": "Four-vendor LACP regression"}).json()
                            aggregation_project_id = aggregation_project["id"]
                            files = [("files", (path.name, path.read_bytes(), "application/octet-stream")) for path in sorted((repo / "samples" / "fixtures").glob("lag-*.cfg"))]
                            assert len(files) == 4
                            response = client.post(f"api/projects/{aggregation_project_id}/imports", files=files, data={"expected_version": str(aggregation_project["version"])})
                            response.raise_for_status()
                            job_id = response.json()["id"]
                            wait_for_import(client, job_id)
                            routing = checked_json(client.post("api/projects", json={"name": "Four-vendor IGP / LSDB / AI packaging smoke"}))
                            routing_project_id = routing["id"]
                            routing_job = checked_json(client.post(f"api/projects/{routing_project_id}/demo?kind=multi-vendor-igp"))
                            wait_for_import(client, routing_job["id"])
                            check_renamed_zip_merge(client, routing_project_id, repo)
                            # Each endpoint reads a native dataset from the EXE's bundled samples.
                            for vendor in ("huawei", "cisco", "juniper", "nokia"):
                                for protocol in ("ospf", "isis"):
                                    current = checked_json(client.get(f"api/projects/{routing_project_id}"))
                                    imported = checked_json(client.post(f"api/projects/{routing_project_id}/lsdb/demo?vendor={vendor}&protocol={protocol}", json={"version": current["version"]}))
                                    native_batches.append(imported["batch"])
                            settings = checked_json(client.get("api/ai/settings"))
                            settings.update(backend="openai", model="packaging-smoke-model", api_url="http://127.0.0.1:9/v1",
                                            api_key_env="IPTOPO_SMOKE_API_KEY", api_protocol="chat_completions", timeout_seconds=45)
                            if args.codex_exe:
                                settings["codex_path"] = str(args.codex_exe)
                            saved_ai_settings = checked_json(client.put("api/ai/settings", json=settings))
                            assert "_codex_home" not in saved_ai_settings
                            session = checked_json(client.post(f"api/projects/{routing_project_id}/chat/sessions", json={"backend": "openai", "model": "packaging-smoke-model"}))
                            chat_session_id = session["id"]
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
                        routing = check_routing_and_lsdb(client, routing_project_id, native_batches)
                        assert checked_json(client.get("api/ai/settings")) == saved_ai_settings, "AI connection preferences were not persisted"
                        if args.codex_exe:
                            assert saved_ai_settings["codex_path"] == str(args.codex_exe), "Native Codex path was not persisted"
                            probe = checked_json(client.post("api/ai/probe", json={"backend": "codex"}))
                            assert probe["available"] is False, "Smoke Codex profile should remain unauthenticated"
                            assert probe.get("version"), "The native Codex executable did not return a version"
                            assert "登录" in probe.get("detail", ""), "Native Codex failed before the expected unauthenticated state: "+probe.get("detail", "")
                        history = checked_json(client.get(f"api/projects/{routing_project_id}/chat/sessions/{chat_session_id}"))
                        assert history["session"]["backend"] == "openai" and history["messages"] == []
                        previous_mcp_token = check_readonly_ai(client, routing, previous_mcp_token)
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
            checks = "startup, PC-only frontend and lazy assets, simulation context-menu and ignored-diagnostics UI assets, metadata, eight-device multiprocess import, Junos dual-stack inference, four-vendor LACP ownership, six-device four-vendor IGP, renamed ZIP merge identity and ignored semantics, OSPF/IS-IS cost30 and 50/25/25 ECMP, eight native LSDB datasets and fused paths, AI settings/session persistence, authenticated read-only MCP tools without a model request, exports, single instance, shutdown and restart persistence"
            if args.codex_exe:
                checks += ", native Codex version/protocol and expected unauthenticated account state"
            print("PASS: "+checks)
        except Exception:
            log = data / "desktop.log"
            if log.exists():
                print(log.read_text(encoding="utf-8"), file=sys.stderr)
            raise


if __name__ == "__main__":
    main()
