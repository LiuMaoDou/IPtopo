"""Real HTTP MCP handshake, authentication, tool execution and error semantics."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.app.ai.mcp import register_mcp
from backend.app.ai.query import QueryEngine


class Broker:
    allowed_origins = {"http://testserver"}

    def __init__(self):
        self.engines = {
            "token-a": QueryEngine({"id": "snap-a", "devices": [{"id": "a", "hostname": "Router-A"}]}, project_id="a", revision="rev-a"),
            "token-b": QueryEngine({"id": "snap-b", "devices": [{"id": "b", "hostname": "Router-B"}]}, project_id="b", revision="rev-b"),
        }
        self.events = []

    def resolve(self, token):
        return self.engines.get(token)

    def on_tool(self, token, name, result):
        self.events.append((token, name, result))


@pytest.fixture
def environment():
    app, broker = FastAPI(), Broker()
    register_mcp(app, broker)
    with TestClient(app) as client:
        yield client, broker


def rpc(client, method, params=None, *, request_id=1, token="token-a", headers=None):
    data = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        data["params"] = params
    return client.post("/api/ai/mcp", json=data, headers={"Authorization": "Bearer " + token, **(headers or {})})


@pytest.mark.parametrize("version", ["2025-11-25", "2025-06-18"])
def test_initialize_notifications_ping_catalog_and_structured_results(environment, version):
    client, broker = environment
    response = rpc(client, "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == version
    assert response.json()["result"]["capabilities"]["tools"]["listChanged"] is False
    notification = client.post("/api/ai/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"Authorization": "Bearer token-a"})
    assert notification.status_code == 202 and not notification.content
    assert rpc(client, "ping").json()["result"] == {}
    assert len(rpc(client, "tools/list").json()["result"]["tools"]) == 9
    result = rpc(client, "tools/call", {"name": "network_summary", "arguments": {}}).json()["result"]
    assert not result["isError"]
    assert result["structuredContent"] == json.loads(result["content"][0]["text"])
    assert result["structuredContent"]["context"]["revision"] == "rev-a"
    assert result["structuredContent"]["data"]["device_count"] == 1
    assert broker.events[-1][:2] == ("token-a", "network_summary")


def test_tokens_scope_all_queries_and_can_be_revoked(environment):
    client, broker = environment
    result = rpc(client, "tools/call", {"name": "search_devices", "arguments": {}}, token="token-b").json()["result"]
    assert result["structuredContent"]["data"]["devices"][0]["hostname"] == "Router-B"
    result = rpc(client, "tools/call", {"name": "device_details", "arguments": {"device_id": "b"}}, token="token-a").json()["result"]
    assert result["isError"] and result["structuredContent"]["diagnostics"][0]["code"] == "device_not_found"
    del broker.engines["token-a"]
    assert rpc(client, "tools/list").status_code == 401


def test_authentication_and_origin_are_enforced_on_all_methods(environment):
    client, _ = environment
    assert client.post("/api/ai/mcp", json={}).status_code == 401
    assert rpc(client, "ping", token="missing").status_code == 401
    assert rpc(client, "ping", headers={"Origin": "https://evil.example"}).status_code == 403
    assert rpc(client, "ping", headers={"Origin": "null"}).status_code == 403
    assert rpc(client, "ping", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.get("/api/ai/mcp").status_code == 401
    assert client.get("/api/ai/mcp", headers={"Authorization": "Bearer token-a"}).status_code == 405
    assert client.get("/api/ai/mcp", headers={"Authorization": "Bearer token-a", "Origin": "https://evil.example"}).status_code == 403


def test_default_origin_policy_requires_same_loopback_origin():
    class LocalBroker:
        def resolve(self, _):
            return QueryEngine({}, project_id="p", revision="r")
    app = FastAPI()
    register_mcp(app, LocalBroker())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert rpc(client, "ping", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
        assert rpc(client, "ping", headers={"Origin": "http://127.0.0.1:9999"}).status_code == 403
        assert rpc(client, "ping", headers={"Origin": "http://evil.example", "Host": "evil.example"}).status_code == 403


def test_protocol_errors_are_distinct_from_actionable_tool_errors(environment):
    client, _ = environment
    assert rpc(client, "missing").json()["error"]["code"] == -32601
    assert rpc(client, "tools/call", {"name": "shell", "arguments": {}}).json()["error"]["code"] == -32602
    assert rpc(client, "tools/call", {"name": "network_summary", "arguments": []}).json()["error"]["code"] == -32602
    invalid_args = rpc(client, "tools/call", {"name": "search_devices", "arguments": {"limit": 999}}).json()
    assert "error" not in invalid_args and invalid_args["result"]["isError"]
    assert invalid_args["result"]["structuredContent"]["diagnostics"][0]["code"] == "invalid_arguments"
    malformed = client.post("/api/ai/mcp", content=b"{", headers={"Authorization": "Bearer token-a", "Content-Type": "application/json"})
    assert malformed.json()["error"]["code"] == -32700
    batch = client.post("/api/ai/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers={"Authorization": "Bearer token-a"})
    assert batch.status_code == 400 and batch.json()["error"]["code"] == -32600
    assert rpc(client, "ping", headers={"MCP-Protocol-Version": "1900-01-01"}).status_code == 400


def test_notifications_are_empty_and_body_size_is_bounded(environment):
    client, _ = environment
    headers = {"Authorization": "Bearer token-a", "Content-Type": "application/json"}
    large = client.post("/api/ai/mcp", content=b" " * (65536 + 1), headers=headers)
    assert large.status_code == 413
    invalid = client.post("/api/ai/mcp", json={"jsonrpc": "2.0", "method": "tools/call"}, headers=headers)
    assert invalid.status_code == 400
    cancelled = client.post("/api/ai/mcp", json={"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}, headers=headers)
    assert cancelled.status_code == 202 and not cancelled.content


def test_query_rate_is_bounded_per_token(environment, monkeypatch):
    client, _ = environment
    monkeypatch.setattr("backend.app.ai.mcp.MAX_CALLS_PER_MINUTE", 2)
    params = {"name": "network_summary", "arguments": {}}
    assert rpc(client, "tools/call", params).status_code == 200
    assert rpc(client, "tools/call", params).status_code == 200
    limited = rpc(client, "tools/call", params)
    assert limited.status_code == 429 and limited.json()["error"]["code"] == -32000
    assert rpc(client, "tools/call", params, token="token-b").status_code == 200
