"""Real process-loss recovery and backups above the former import-size limit.

All processes, database rows and source files belong to isolated temporary stores;
this suite does not start, restart or contact the user's port 8765 service.
"""
from hashlib import sha256
import multiprocessing
import os
import signal

from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.main import create_app
from backend.app.store import Source, uid
from backend.tests.test_api import project, upload
from backend.tests.test_worker_process import (
    TERMINAL, batch_zip, configuration, running_with_partial_results,
    start_upload, wait_until,
)


def test_killed_real_parser_is_reaped_without_restart_and_same_project_can_retry(tmp_path, monkeypatch):
    # A spawned interpreter also imports backend.app.main. Keep its module-level
    # default Store inside this temporary directory, not the developer's data.
    directory = tmp_path / "isolated-killed-worker"
    monkeypatch.setenv("IPTOPO_DATA_DIR", str(directory))
    app = create_app(directory, worker_mode="process")
    with TestClient(app) as client:
        original = project(client, "Process loss recovery")
        first = start_upload(client, original, "initial.cfg", configuration(9000))
        complete = wait_until(lambda: client.get(f"/api/imports/{first['id']}").json(),
                              lambda row: row["status"] in TERMINAL,
                              description="initial worker completion")
        assert complete["status"] == "completed", complete
        current = client.get(f"/api/projects/{original['id']}").json()
        baseline = {key: current[key] for key in ("snapshot_id", "version", "device_count")}
        prior_children = {child.pid for child in multiprocessing.active_children()}

        interrupted = start_upload(client, current, "partial.zip", batch_zip())
        partial = running_with_partial_results(client, interrupted["id"])
        assert 0 < partial["progress"] < 90
        spawned = [child for child in multiprocessing.active_children() if child.pid not in prior_children]
        assert len(spawned) == 1
        child = spawned[0]
        assert type(child).__name__ == "SpawnProcess"
        assert child.daemon is True and child.pid != os.getpid()
        assert child.is_alive()
        if os.name == "posix":
            os.kill(child.pid, 0)  # Confirm the actual OS child exists before SIGKILL.
        child.kill()
        child.join(timeout=5)
        assert not child.is_alive() and child.exitcode != 0
        if os.name == "posix":
            assert child.exitcode == -signal.SIGKILL

        # This request exercises the real HTTP middleware reaper. No service
        # restart, mock process, manual job-state change or database repair.
        recovered = client.get(f"/api/imports/{interrupted['id']}").json()
        assert recovered["status"] == "interrupted", recovered
        assert recovered["errors"]
        assert not (directory / "uploads" / interrupted["id"]).exists()
        cancelled = client.post(f"/api/imports/{interrupted['id']}/cancel").json()
        assert cancelled["status"] == "interrupted"
        unchanged = client.get(f"/api/projects/{original['id']}").json()
        assert {key: unchanged[key] for key in baseline} == baseline
        assert len(unchanged["snapshots"]) == 1
        # A second request must not relabel a normally completed worker.
        assert client.get(f"/api/imports/{first['id']}").json()["status"] == "completed"

        retry = start_upload(client, unchanged, "retry.cfg", configuration(9001))
        retried = wait_until(lambda: client.get(f"/api/imports/{retry['id']}").json(),
                             lambda row: row["status"] in TERMINAL,
                             description="worker after process-loss recovery")
        assert retried["status"] == "completed", retried
        final = client.get(f"/api/projects/{original['id']}").json()
        assert final["version"] == baseline["version"] + 1
        assert final["device_count"] == 2
        assert len(final["snapshots"]) == 2


def test_own_backup_above_former_66_mib_limit_round_trips_facts_and_sources(tmp_path):
    app = create_app(tmp_path / "large-backup", worker_mode="inline")
    with TestClient(app) as client:
        _, original = upload(client, project(client, "Accumulated historical sources"))
        store = app.state.store
        # Jobs retain historical source rows. Materialize seventeen historical
        # 4-MiB files directly to avoid rerunning large unrelated parser fixtures.
        # This is an ordinary exportable persisted state, reached across imports.
        payload = "! historical source\n" + "x" * (4 * 1024 * 1024 - 20)
        digest = sha256(payload.encode("utf-8")).hexdigest()
        source_ids = []
        with store.session(write=True) as session:
            for index in range(17):
                identifier = f"historical-source-{index}"
                storage_key = uid() + ".txt"
                # Byte fixtures retain the intended LF content on Windows too;
                # text-mode newline translation would invalidate the digest.
                (store.root / "sources" / storage_key).write_bytes(payload.encode("utf-8"))
                session.add(Source(key=uid(), project_id=original["id"], source_id=identifier,
                                   filename=f"historical-{index}.cfg", storage_key=storage_key, encoding="utf-8"))
                source_ids.append(identifier)
        backup = client.get(f"/api/projects/{original['id']}/exports/json?include_sources=true")
        assert backup.status_code == 200, backup.text[:200]
        # This is an actual >68-MiB file, not a test that lowers the old limit.
        assert len(backup.content) > 68 * 1024 * 1024
        assert len(backup.content) > main_module.MAX_UPLOAD_BYTES  # Previous restore rejected this.
        assert len(backup.content) < main_module.MAX_RESTORE_BYTES
        restored = client.post("/api/projects/restore", files={"file": ("own-backup.iptopo.json", backup.content, "application/json")})
        assert restored.status_code == 201, restored.text[:200]
        copy = restored.json()
        assert copy["id"] != original["id"]
        assert copy["device_count"] == original["device_count"] == 2
        assert {d["hostname"] for d in copy["snapshot"]["devices"]} == {"A", "B"}
        assert copy["snapshot"]["project_id"] == copy["id"]
        assert len(copy["snapshots"]) == len(original["snapshots"])
        for identifier in (source_ids[0], source_ids[-1]):
            source = client.get(f"/api/projects/{copy['id']}/sources/{identifier}")
            assert source.status_code == 200
            assert sha256(source.json()["text"].encode("utf-8")).hexdigest() == digest
        # Source and current facts still belong to their original project too.
        assert client.get(f"/api/projects/{original['id']}").json()["snapshot_id"] == original["snapshot_id"]


def test_export_and_restore_enforce_the_same_configured_backup_limit(tmp_path, monkeypatch):
    app = create_app(tmp_path / "configured-limit", worker_mode="inline")
    with TestClient(app) as client:
        _, original = upload(client, project(client, "Backup size guard"))
        monkeypatch.setattr(main_module, "MAX_RESTORE_BYTES", 1024)
        before = client.get("/api/projects").json()
        rejected = client.get(f"/api/projects/{original['id']}/exports/json?include_sources=true")
        assert rejected.status_code == 413
        assert "恢复限额" in rejected.json()["detail"]
        too_large = client.post("/api/projects/restore", files={"file": ("oversize.json", b" " * 1025, "application/json")})
        assert too_large.status_code == 413
        assert client.get("/api/projects").json() == before
        assert client.get(f"/api/projects/{original['id']}").json()["snapshot_id"] == original["snapshot_id"]
