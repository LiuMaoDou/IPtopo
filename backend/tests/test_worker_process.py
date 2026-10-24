"""Exercise real Uvicorn + spawned parser processes with isolated disk/ports.

No mocks, production database, fixed port, or sleep-based completion assumptions.
The large batches give the HTTP client time to interrupt work between files.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import ipaddress
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import zipfile

import httpx
import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
TERMINAL = {"completed", "cancelled", "failed", "conflict", "interrupted"}


def configuration(index: int) -> bytes:
    address = ipaddress.IPv4Address(int(ipaddress.IPv4Address("10.40.0.0")) + index * 4 + 1)
    return f"version 15.2\nhostname Worker-{index}\ninterface GigabitEthernet0/0\n ip address {address} 255.255.255.252\n no shutdown\n!\nend\n".encode()


def batch_zip(count=1200) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(count):
            archive.writestr(f"router-{index:04}.cfg", configuration(index))
    return buffer.getvalue()


def wait_until(read, accept, *, timeout=25, description):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = read()
        if accept(last):
            return last
        time.sleep(0.025)
    pytest.fail(f"Timed out waiting for {description}; last state: {last}")


@contextmanager
def live_server(tmp_path):
    """Pass an already-bound ephemeral socket to avoid port reservation races."""
    if os.name != "posix":
        pytest.skip("The subprocess harness uses POSIX socket inheritance and signals")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    assert port != 8765
    data_dir = tmp_path / "isolated-worker-data"
    log_path = tmp_path / f"uvicorn-{port}.log"
    environment = {**os.environ, "IPTOPO_DATA_DIR": str(data_dir), "PYTHONUNBUFFERED": "1"}
    for setting in ("IPTOPO_MAX_FILES", "IPTOPO_MAX_FILE_BYTES", "IPTOPO_MAX_TOTAL_BYTES", "IPTOPO_MAX_ZIP_DEPTH"):
        environment.pop(setting, None)
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--fd", str(listener.fileno()), "--log-level", "warning"],
            cwd=REPOSITORY, env=environment, pass_fds=(listener.fileno(),),
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        listener.close()
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
            try:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        pytest.fail(f"Isolated server exited before readiness: {log_path.read_text()}")
                    try:
                        if client.get("/api/health", timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        pytest.fail(f"Server did not become ready: {log_path.read_text()}")
                    time.sleep(0.025)
                yield client
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                assert process.returncode in (0, -signal.SIGTERM), log_path.read_text()


def start_upload(client, project, filename, content):
    response = client.post(
        f"/api/projects/{project['id']}/imports",
        files={"files": (filename, content, "application/octet-stream")},
        data={"expected_version": str(project["version"]), "strategy": "merge"},
    )
    assert response.status_code == 202, response.text
    return response.json()


def running_with_partial_results(client, job_id):
    job = wait_until(
        lambda: client.get(f"/api/imports/{job_id}").json(),
        lambda item: (item["status"] == "running" and 0 < item["progress"] < 90) or item["status"] in TERMINAL,
        description="worker to parse at least one file before interruption",
    )
    assert job["status"] == "running", f"Expected interruptible batch, got {job['status']}"
    assert job["results"], "The real worker must have parsed files before interruption"
    return job


def test_spawned_worker_commits_atomically_cancel_and_restart_preserve_previous_snapshot(tmp_path):
    archive = batch_zip()
    with live_server(tmp_path) as client:
        response = client.post("/api/projects", json={"name": "Real spawned worker smoke"})
        assert response.status_code == 201, response.text
        project = response.json()
        job = start_upload(client, project, "initial.cfg", configuration(9000))
        finished = wait_until(lambda: client.get(f"/api/imports/{job['id']}").json(), lambda j: j["status"] in TERMINAL, description="spawned worker completion")
        assert finished["status"] == "completed", finished
        assert finished["progress"] == 100
        project = client.get(f"/api/projects/{project['id']}").json()
        baseline_snapshot = project["snapshot_id"]
        baseline_version = project["version"]
        baseline_devices = project["snapshot"]["devices"]
        assert len(baseline_devices) == 1

        cancellation = start_upload(client, project, "cancel-this.zip", archive)
        running_with_partial_results(client, cancellation["id"])
        response = client.post(f"/api/imports/{cancellation['id']}/cancel")
        assert response.status_code == 200, response.text
        cancelled = wait_until(lambda: client.get(f"/api/imports/{cancellation['id']}").json(), lambda j: j["status"] in TERMINAL, description="worker cancellation")
        assert cancelled["status"] == "cancelled", cancelled
        assert 0 < cancelled["progress"] < 100
        current = client.get(f"/api/projects/{project['id']}").json()
        assert current["snapshot_id"] == baseline_snapshot
        assert current["version"] == baseline_version
        assert current["snapshot"]["devices"] == baseline_devices
        assert len(current["snapshots"]) == 1

        interrupted = start_upload(client, current, "interrupt-this.zip", archive)
        running_with_partial_results(client, interrupted["id"])
        # Closing this context sends SIGTERM to the isolated server; its lifespan
        # terminates its actual child worker without publishing partial facts.

    with live_server(tmp_path) as client:
        recovered = client.get(f"/api/imports/{interrupted['id']}").json()
        assert recovered["status"] == "interrupted", recovered
        assert recovered["errors"]
        current = client.get(f"/api/projects/{project['id']}").json()
        assert current["snapshot_id"] == baseline_snapshot
        assert current["version"] == baseline_version
        assert current["snapshot"]["devices"] == baseline_devices
        assert len(current["snapshots"]) == 1
        # Interrupted jobs do not lock the project: a fresh real worker can commit.
        replacement = start_upload(client, current, "after-restart.cfg", configuration(9001))
        completed = wait_until(lambda: client.get(f"/api/imports/{replacement['id']}").json(), lambda j: j["status"] in TERMINAL, description="post-restart worker completion")
        assert completed["status"] == "completed", completed
        final = client.get(f"/api/projects/{project['id']}").json()
        assert final["version"] == baseline_version + 1
        assert len(final["snapshot"]["devices"]) == 2
