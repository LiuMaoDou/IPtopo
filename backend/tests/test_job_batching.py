"""Progress batching preserves per-file outcomes and terminal failure evidence."""
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from backend.app import jobs
from backend.app.store import Job, Project, Store


def prepared_job(tmp_path, count):
    store = Store(tmp_path)
    folder = store.root / "uploads" / "job"
    folder.mkdir()
    uploads = []
    for n in range(count):
        key = f"router-{n}.cfg"
        (folder / key).write_text(f"hostname r{n}\ninterface Loopback0\n ip address 192.0.2.{n+1}/32\n!\nend\n")
        uploads.append({"filename": key, "storage_key": key, "hash": str(n), "encoding": "utf-8"})
    with store.session(write=True) as s:
        s.add(Project(id="project", name="Batch test", version=1, settings={}, overrides=[], views=[]))
        s.add(Job(id="job", project_id="project", status="queued", progress=0,
                  data={"uploads": uploads, "results": [{"filename": "metadata", "status": "skipped"}], "errors": [], "expected_version": 1}))
    return store, folder


def test_batching_reduces_progress_writes_and_flushes_last_partial_batch(tmp_path, monkeypatch):
    store, _ = prepared_job(tmp_path, 53)
    monkeypatch.setattr(jobs, "Store", lambda _: store)
    # Isolate the count trigger independently of runner/CI CPU speed.
    monkeypatch.setattr(jobs, "PROGRESS_INTERVAL_SECONDS", 10_000)
    writes = []
    @event.listens_for(store.engine, "before_cursor_execute")
    def track(_, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE jobs SET"):
            writes.append(statement)
    jobs.import_worker(str(tmp_path), "job")
    with store.session() as s:
        job = s.get(Job, "job")
        assert job.status == "completed"
        assert job.progress == 100
        assert len(job.data["results"]) == 54
        assert {x["filename"] for x in job.data["results"][1:]} == {f"router-{n}.cfg" for n in range(53)}
        assert s.get(Project, "project").version == 2
    # running + batches 25,25,3 + completed. Per-file implementation used55.
    assert len(writes) == 5
    store.engine.dispose()


def test_worker_exception_flushes_uncommitted_results_without_publishing_snapshot(tmp_path, monkeypatch):
    store, folder = prepared_job(tmp_path, 4)
    (folder / "router-3.cfg").unlink()
    monkeypatch.setattr(jobs, "Store", lambda _: store)
    monkeypatch.setattr(jobs, "PROGRESS_INTERVAL_SECONDS", 10_000)
    with pytest.raises(FileNotFoundError):
        jobs.import_worker(str(tmp_path), "job")
    with store.session() as s:
        job = s.get(Job, "job")
        assert job.status == "failed"
        assert len(job.data["results"]) == 4  # initial skipped entry + 3 parsed
        assert job.data["errors"]
        assert 0 < job.progress < 100
        project = s.get(Project, "project")
        assert project.snapshot_id is None
        assert project.version == 1
    assert not folder.exists()
    store.engine.dispose()
