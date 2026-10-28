"""Independent regressions for chat lifecycle races and source freezing."""
import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
from threading import Event

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.store import Observation, Project
from backend.tests.test_api import config, project, upload
from backend.tests.test_lsdb import batch


def test_delete_cannot_slip_between_message_commit_and_run_registration(tmp_path):
    app = create_app(tmp_path, worker_mode="inline")
    state = {}
    delete_submitted, provider_started = Event(), Event()

    async def provider(settings, **kwargs):
        state["loop"] = asyncio.get_running_loop()
        state["release"] = asyncio.Event()
        provider_started.set()
        yield {"type": "delta", "text": "正在查询"}
        await state["release"].wait()
        yield {"type": "done"}

    app.state.ai_stream_turn = provider
    with TestClient(app, raise_server_exceptions=False) as first:
        p = project(first, "Concurrent session deletion")
        base = f"/api/projects/{p['id']}"
        session = first.post(base + "/chat/sessions", json={"backend": "codex"}).json()
        url = base + f"/chat/sessions/{session['id']}"
        # A separate ASGI client can service the deletion while the first
        # client's database commit callback is parked at the vulnerable boundary.
        second = TestClient(app, raise_server_exceptions=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            def after_commit(_session):
                if state.get("checkpoint"):
                    return
                state["checkpoint"] = True
                state["delete"] = pool.submit(second.delete, url)
                delete_submitted.set()
                try:
                    state["delete"].result(timeout=1)
                except FutureTimeout:
                    # Correct locking may block DELETE until run registration;
                    # release the checkpoint so that the valid request proceeds.
                    pass

            event.listen(Session, "after_commit", after_commit)
            pending = pool.submit(first.post, url + "/messages", json={"text": "介绍当前网络"})
            try:
                assert delete_submitted.wait(timeout=5)
                deleted = state["delete"].result(timeout=5)
                assert provider_started.wait(timeout=5)
                assert deleted.status_code == 409, deleted.text
            finally:
                try:
                    if provider_started.wait(timeout=5):
                        state["loop"].call_soon_threadsafe(state["release"].set)
                    response = pending.result(timeout=5)
                finally:
                    event.remove(Session, "after_commit", after_commit)
                    second.close()
            assert response.status_code == 200
            rows = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert rows[-1]["type"] == "done"
            assert rows[-1]["message"]["status"] == "completed"
            assert app.state.ai_runs == {}
            assert first.get(url).status_code == 200


def test_frozen_mcp_sources_survive_reimport_and_reject_later_source_ids(tmp_path):
    app = create_app(tmp_path, worker_mode="inline")
    with TestClient(app) as client:
        _, initial = upload(client, project(client, "Frozen source versions"))
        base = f"/api/projects/{initial['id']}"
        old_source = next(row for row in initial["snapshot"]["source_files"] if row["filename"] == "a.cfg")
        grant = client.post(base + "/chat/mcp-token", json={"ttl_minutes": 1}).json()

        def source_from(token, source_id):
            response = client.post("/api/ai/mcp", headers={"Authorization": "Bearer " + token},
                                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "get_evidence", "arguments": {"source_file_id": source_id, "line": 1, "end_line": 12}}})
            assert response.status_code == 200
            return response.json()["result"]["structuredContent"]

        _, updated = upload(client, initial, [("a.cfg", config("A", "10.1.0.1"))])
        new_source = next(row for row in updated["snapshot"]["source_files"] if row["filename"] == "a.cfg")
        assert new_source["id"] != old_source["id"]
        assert updated["effective_revision"] != grant["context"]["revision"]
        old = source_from(grant["token"], old_source["id"])
        assert old["status"] == "ok"
        assert old["context"]["revision"] == grant["context"]["revision"]
        old_text = "\n".join(line["text"] for line in old["data"]["lines"])
        assert "10.0.0.1" in old_text and "10.1.0.1" not in old_text
        assert "PRIVATE-SECRET" not in old_text
        assert old["evidence"][0]["source_sha256"] == old_source["hash"]
        denied = source_from(grant["token"], new_source["id"])
        assert denied["status"] == "error" and denied["diagnostics"][0]["code"] == "source_not_found"

        fresh = client.post(base + "/chat/mcp-token", json={"ttl_minutes": 1}).json()
        new = source_from(fresh["token"], new_source["id"])
        assert new["status"] == "ok"
        assert "10.1.0.1" in "\n".join(line["text"] for line in new["data"]["lines"])
        assert new["context"]["revision"] == updated["effective_revision"]
        assert new["evidence"][0]["source_sha256"] == new_source["hash"]


def test_frozen_revision_and_lsdb_metadata_share_one_database_read_snapshot(tmp_path):
    app = create_app(tmp_path, worker_mode="inline")
    with TestClient(app) as client:
        initial = project(client, "Atomic query context")
        identifier = initial["id"]
        checkpoint = {}

        def concurrent_lsdb_commit(_connection, _cursor, statement, _parameters, _context, _many):
            if checkpoint or "json_remove" not in statement or "lsdb_observations" not in statement:
                return
            checkpoint["committed"] = True
            # Commit through a separate actual connection after the freeze has
            # read Project/revision but before its LSDB metadata query executes.
            # WAL permits this writer even when the freeze owns a read snapshot.
            with app.state.store.session(write=True) as session:
                observation = batch(id="later-observation").model_dump(mode="json")
                session.add(Observation(id=observation["id"], project_id=identifier, data=observation))
                row = session.get(Project, identifier)
                row.settings = {**row.settings, "lsdb_revision": "committed-after-context-read"}
                row.version += 1

        event.listen(app.state.store.engine, "before_cursor_execute", concurrent_lsdb_commit)
        try:
            response = client.post(f"/api/projects/{identifier}/chat/mcp-token", json={})
        finally:
            event.remove(app.state.store.engine, "before_cursor_execute", concurrent_lsdb_commit)
        assert checkpoint.get("committed")
        assert response.status_code == 201, response.text
        grant = response.json()
        result = client.post("/api/ai/mcp", headers={"Authorization": "Bearer " + grant["token"]},
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "network_summary", "arguments": {}}}).json()["result"]["structuredContent"]
        assert grant["context"]["revision"] == initial["effective_revision"]
        assert grant["context"]["project_version"] == initial["version"]
        assert result["data"]["lsdb_batch_count"] == 0  # The old revision had no batch.
        current = client.get(f"/api/projects/{identifier}").json()
        assert current["effective_revision"] != initial["effective_revision"]
        assert current["version"] == initial["version"] + 1
