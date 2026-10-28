"""Chat/MCP integration exercises isolated stores with an injected Agent transport.

The deterministic provider below verifies orchestration, not real model quality.
Native Agent transports have separate protocol and real runtime checks.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.ai.providers import ProviderError
from backend.app.store import ChatMessage, ChatSession, now


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path, worker_mode="inline")
    calls = []

    async def provider(config, **kw):
        calls.append(kw)
        result = await kw["execute_tool"]("network_summary", {})
        assert result["status"] in {"ok", "incomplete"}
        yield {"type": "thread", "thread_id": "native-test-thread"}
        yield {"type": "delta", "text": "已查询当前项目。"}
        yield {"type": "usage", "usage": {"input_tokens": 5}}
        yield {"type": "done"}

    app.state.ai_stream_turn = provider
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "AI integration"}).json()
        base = f"/api/projects/{project['id']}"
        session = client.post(base+"/chat/sessions", json={"backend": "codex"}).json()
        yield app, client, project, base, session, calls


def test_chat_tools_cards_history_and_project_version_are_independent(setup):
    app, client, project, base, session, calls = setup
    version = project["version"]
    output = events(client.post(base+f"/chat/sessions/{session['id']}/messages", json={"text": "介绍一下组网"}))
    assert output[0]["type"] == "start"
    assert any(row["type"] == "tool" and row["name"] == "network_summary" for row in output)
    final = output[-1]["message"]
    assert final["status"] == "completed" and final["content"] == "已查询当前项目。"
    assert final["cards"][0]["kind"] == "summary"
    assert final["tool_calls"][0]["name"] == "network_summary"
    assert final["usage"]["input_tokens"] == 5
    saved = client.get(base+f"/chat/sessions/{session['id']}").json()
    assert [row["role"] for row in saved["messages"]] == ["user", "assistant"]
    assert saved["current_context"]["revision"] == project["effective_revision"]
    assert client.get(base).json()["version"] == version
    assert app.state.ai_runs == {}
    assert app.state.ai_broker.resolve(calls[0]["mcp_token"]) is None


def test_fact_revision_changes_but_view_save_keeps_native_conversation(setup):
    app, client, project, base, session, calls = setup
    path = base+f"/chat/sessions/{session['id']}/messages"
    events(client.post(path, json={"text": "介绍网络"}))
    view = {"id": "test", "name": "view", "positions": {}}
    updated = client.put(base+"/views/test?summary=true", json={"version": project["version"], "view": view}).json()
    events(client.post(path, json={"text": "再查一次"}))
    assert calls[1]["thread_id"] == "native-test-thread"
    assert len(calls[1]["history"]) == 2
    current = client.post(base+"/overrides", json={"version": updated["version"], "operation": "add_device",
                         "value": {"hostname": "Manual R1"}}).json()
    assert current["effective_revision"] != project["effective_revision"]
    events(client.post(path, json={"text": "现在有哪些设备"}))
    assert calls[2]["thread_id"] is None
    stored = client.get(base+f"/chat/sessions/{session['id']}").json()
    assert stored["messages"][1]["context"]["revision"] != stored["current_context"]["revision"]


def test_sessions_and_selection_cannot_cross_projects(setup):
    app, client, project, base, session, calls = setup
    other = client.post("/api/projects", json={"name": "Another"}).json()
    wrong = f"/api/projects/{other['id']}/chat/sessions/{session['id']}"
    assert client.get(wrong).status_code == 404
    assert client.post(wrong+"/messages", json={"text": "devices"}).status_code == 404
    assert client.post(base+f"/chat/sessions/{session['id']}/messages", json={"text": "this device", "selection": {"kind":"device", "id":"foreign"}}).status_code == 422
    assert not calls


def test_mcp_external_grant_is_scoped_frozen_and_revocable(setup):
    app, client, project, base, session, calls = setup
    grant = client.post(base+"/chat/mcp-token", json={"ttl_minutes": 1})
    assert grant.status_code == 201, grant.text
    data = grant.json()
    token = data["token"]
    headers = {"Authorization": "Bearer "+token}
    output = client.post("/api/ai/mcp", headers=headers, json={"jsonrpc":"2.0","id":1,"method":"tools/call", "params":{"name":"network_summary","arguments":{}}})
    assert output.status_code == 200
    assert output.json()["result"]["structuredContent"]["context"]["revision"] == data["context"]["revision"]
    client.post(base+"/overrides", json={"version":project["version"],"operation":"add_device","value":{"hostname":"new"}})
    frozen = client.post("/api/ai/mcp", headers=headers, json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"network_summary","arguments":{}}}).json()
    assert frozen["result"]["structuredContent"]["context"]["revision"] == data["context"]["revision"]
    app.state.ai_broker.revoke(token)
    assert client.post("/api/ai/mcp", headers=headers, json={"jsonrpc":"2.0","id":3,"method":"ping"}).status_code == 401


def test_chat_origin_guards_and_configuration_secret_references(setup):
    app, client, project, base, session, calls = setup
    settings = client.get("/api/ai/settings").json()
    assert "api_key" not in settings
    assert client.put("/api/ai/settings", json=settings, headers={"Origin":"https://evil.example"}).status_code == 403
    assert client.post(base+"/chat/mcp-token", json={}, headers={"Origin":"https://evil.example"}).status_code == 403
    assert client.put("/api/ai/settings", json={**settings,"api_key":"private"}).status_code == 422
    assert client.put("/api/ai/settings", json={**settings,"model":"test"}).status_code == 200
    assert client.get(base).json()["version"] == project["version"]
    # A rebinding hostname must not become trusted just because Host matches it.
    assert client.post(base+"/chat/mcp-token", json={}, headers={"Origin":"http://attacker.example", "Host":"attacker.example"}).status_code == 403
    assert client.post(base+"/chat/mcp-token", json={}, headers={"Origin":"http://[invalid"}).status_code == 403


def test_exhausted_tool_grants_do_not_leave_a_running_message_or_lock(setup):
    app, client, project, base, session, calls = setup
    from backend.app.ai.query import QueryEngine
    engine = QueryEngine({"id":"empty"}, project_id=project["id"], revision="test")
    tokens = [app.state.ai_broker.issue(engine) for _ in range(128)]
    response = client.post(base+f"/chat/sessions/{session['id']}/messages", json={"text":"介绍项目"})
    assert response.status_code == 429
    assert app.state.ai_runs == {}
    data = client.get(base+f"/chat/sessions/{session['id']}").json()
    assert data["messages"][-1]["status"] == "error"
    for token in tokens:
        app.state.ai_broker.revoke(token)
    assert events(client.post(base+f"/chat/sessions/{session['id']}/messages", json={"text":"重试"}))[-1]["message"]["status"] == "completed"


def test_provider_error_is_persisted_as_incomplete_answer(setup):
    app, client, project, base, session, calls = setup

    async def failing(config, **kw):
        yield {"type":"delta", "text":"开始查询"}
        raise ProviderError("Agent 连接中断", "connection")

    app.state.ai_stream_turn = failing
    output = events(client.post(base+f"/chat/sessions/{session['id']}/messages", json={"text":"查询设备"}))
    assert any(row["type"] == "error" for row in output)
    assert output[-1]["message"]["status"] == "error"
    assert output[-1]["message"]["content"] == "开始查询"
    assert not app.state.ai_runs


def test_cancel_stops_backend_worker_and_persists_partial_response(setup):
    app, client, project, base, session, calls = setup

    async def waiting(config, **kw):
        yield {"type":"delta", "text":"部分回答"}
        await asyncio.Event().wait()

    app.state.ai_stream_turn = waiting
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, base+f"/chat/sessions/{session['id']}/messages", json={"text":"等待停止"})
        deadline = time.monotonic()+5
        while not app.state.ai_runs and time.monotonic()<deadline:
            time.sleep(.01)
        assert app.state.ai_runs
        run_id = next(iter(app.state.ai_runs))
        assert client.delete(base+f"/chat/sessions/{session['id']}").status_code == 409
        duplicate = client.post(base+f"/chat/sessions/{session['id']}/messages", json={"text":"同时请求"})
        assert duplicate.status_code == 409
        stopped = client.post(base+f"/chat/runs/{run_id}/cancel")
        assert stopped.status_code == 200, stopped.text
        output = events(pending.result(timeout=5))
    assert output[-1]["message"]["status"] == "cancelled"
    assert not app.state.ai_runs


def test_restart_marks_only_running_chat_messages_interrupted(tmp_path):
    app = create_app(tmp_path, worker_mode="inline")
    with app.state.store.session(write=True) as s:
        s.add(ChatMessage(id="interrupted", session_id="test", project_id="test", role="assistant", content="partial", status="running", context={}, cards=[], tool_calls=[], usage={}, created_at=now()))
    with TestClient(app):
        with app.state.store.session() as s:
            row = s.get(ChatMessage, "interrupted")
            assert row.status == "error" and "重新启动" in row.error


def test_long_session_keeps_latest_messages_in_chronological_order(setup):
    app, client, project, base, session, calls = setup
    with app.state.store.session(write=True) as s:
        for index in range(503):
            s.add(ChatMessage(id=f"long-{index:04}", session_id=session["id"], project_id=project["id"],
                role="user", content=f"message {index}", status="completed", context={}, cards=[], tool_calls=[],
                usage={}, created_at=f"2026-10-28T00:{index // 60:02}:{index % 60:02}+00:00"))
    data = client.get(base+f"/chat/sessions/{session['id']}").json()
    assert data["messages_truncated"] is True
    assert len(data["messages"]) == 500
    assert data["messages"][0]["content"] == "message 3"
    assert data["messages"][-1]["content"] == "message 502"


def test_mcp_batch_metadata_and_lazy_observation_query(setup):
    app, client, project, base, session, calls = setup
    imported = client.post(base+"/lsdb/demo", json={"version": project["version"]})
    assert imported.status_code == 201, imported.text
    batch = imported.json()["batch"]
    grant = client.post(base+"/chat/mcp-token", json={}).json()
    headers = {"Authorization": "Bearer "+grant["token"]}
    def call(name, arguments):
        response = client.post("/api/ai/mcp", headers=headers, json={"jsonrpc":"2.0","id":1,
            "method":"tools/call","params":{"name":name,"arguments":arguments}})
        assert response.status_code == 200, response.text
        return response.json()["result"]["structuredContent"]
    summary = call("network_summary", {})
    assert summary["data"]["lsdb_batch_count"] == 1
    assert summary["data"]["lsdb_batches"][0]["id"] == batch["id"]
    assert summary["data"]["lsdb_batches"][0]["protocol"] == "ospf"
    targets = call("list_path_targets", {"batch_id": batch["id"]})
    assert targets["status"] in {"ok", "incomplete"}
    assert targets["data"]["nodes"]
    assert targets["context"]["lsdb_batch_id"] == batch["id"]


def test_conversation_order_and_native_history_do_not_depend_on_clock_or_uuid(setup, monkeypatch):
    app, client, project, base, session, calls = setup
    import backend.app.ai.api as chat_api
    monkeypatch.setattr(chat_api, "now", lambda: "2026-10-28T00:00:00+00:00")
    serial = iter(range(10000, 0, -1))
    monkeypatch.setattr(chat_api, "uid", lambda prefix="": prefix+f"{next(serial):032x}")
    path = base+f"/chat/sessions/{session['id']}/messages"
    for question in ("第一轮", "第二轮"):
        final = events(client.post(path, json={"text":question}))[-1]["message"]
        assert final["status"] == "completed"
    saved = client.get(base+f"/chat/sessions/{session['id']}").json()["messages"]
    assert [row["role"] for row in saved] == ["user", "assistant", "user", "assistant"]
    assert [row["content"] for row in saved if row["role"] == "user"] == ["第一轮", "第二轮"]
    assert calls[1]["history"] == [{"role":"user", "content":"第一轮"},
                                    {"role":"assistant", "content":"已查询当前项目。"}]
