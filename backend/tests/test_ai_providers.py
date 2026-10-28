"""Transport acceptance with isolated child processes and HTTP protocol fixtures.

These tests do not contact a paid model or claim to replace actual-agent smoke
checks. They verify protocol sequencing, scopes, completion and cancellation.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

import httpx
import pytest

from backend.app.ai.config import Settings
from backend.app.ai import providers
from backend.app.ai.providers import ProviderCancelled, ProviderError, probe_backend, stream_turn

CATALOG = [{"name": "network_summary", "description": "Project facts", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]


def private_agents(version="v1"):
    return [{"name":"iptopo", "mode":"primary", "native":False,
             "permissions" if version=="v2" else "permission":providers._opencode_permissions("iptopo",version)}]


async def execute_tool(name, arguments):
    assert name == "network_summary" and arguments == {}
    return {"status": "ok", "data": {"device_count": 6}, "cards": []}


async def collect(config, **overrides):
    args = dict(prompt="网络有多少设备？", history=[], thread_id=None, mcp_url="http://127.0.0.1:8765/api/ai/mcp",
                mcp_token="TEST-MCP-TOKEN", execute_tool=execute_tool, cancel=asyncio.Event(), tool_catalog=CATALOG)
    args.update(overrides)
    return [event async for event in stream_turn(config, **args)]


@pytest.fixture
def fake_codex(tmp_path):
    if os.name != "posix":
        pytest.skip("The executable protocol fixture uses a POSIX shebang")
    script = tmp_path/"codex"
    log = tmp_path/"protocol.jsonl"
    script.write_text("#!"+sys.executable+"\n"+'''import json,sys,tomllib
from pathlib import Path
log=Path('''+repr(str(log))+''')
if '--version' in sys.argv:
 print('codex-cli protocol-fixture');sys.exit(0)
def emit(value):
 print(json.dumps(value),flush=True)
config={'mcp_servers':{'global_tool':{'enabled':True,'command':'untrusted-command'}},'features':{}}
for i,a in enumerate(sys.argv):
 if a!='-c':continue
 k,v=sys.argv[i+1].split('=',1);v=tomllib.loads('x='+v)['x']
 if k.startswith('features.'):config['features'][k.split('.',1)[1]]=v
 elif k=='mcp_servers':
  for name,data in v.items():config['mcp_servers'][name]={**config['mcp_servers'].get(name,{}),**data}
 else:config[k]=v
server=next((k for k,v in config['mcp_servers'].items() if k.startswith('iptopo_') and v.get('enabled',True)),None)
for line in sys.stdin:
 d=json.loads(line);m=d.get('method');p=d.get('params',{})
 with log.open('a',encoding='utf-8') as f:f.write(json.dumps({'method':m,'params':p if m in ('thread/start','thread/resume') else {}})+'\\n')
 if m=='initialize':emit({'id':d['id'],'result':{}})
 elif m=='config/read':emit({'id':d['id'],'result':{'config':config}})
 elif m=='mcpServerStatus/list':emit({'id':d['id'],'result':{'data':[{'name':server,'runtimeStatus':'connected','tools':{'network_summary':{'annotations':{'readOnlyHint':True}}}},{'name':'global_tool','runtimeStatus':'disabled','tools':{}}]}})
 elif m=='account/read':emit({'id':d['id'],'result':{'account':{'type':'apikey'},'requiresOpenaiAuth':True}})
 elif m in ('thread/start','thread/resume'):emit({'id':d['id'],'result':{'thread':{'id':'thread-test'}}})
 elif m=='turn/start':
  emit({'id':d['id'],'result':{'turn':{'id':'turn-test'}}})
  text=p['input'][0]['text']
  emit({'method':'item/started','params':{'threadId':'thread-test','turnId':'turn-test','item':{'id':'tool-1','type':'mcpToolCall','server':server,'tool':'network_summary'}}})
  if 'WAIT' in text:continue
  if 'FORBIDDEN' in text:
   emit({'method':'item/started','params':{'threadId':'thread-test','turnId':'turn-test','item':{'id':'bad','type':'commandExecution'}}});continue
  emit({'method':'item/agentMessage/delta','params':{'threadId':'thread-test','turnId':'turn-test','itemId':'answer-1','delta':'6 台设备'}})
  emit({'method':'item/completed','params':{'threadId':'thread-test','turnId':'turn-test','item':{'id':'answer-1','type':'agentMessage','text':'6 台设备'}}})
  emit({'method':'turn/completed','params':{'threadId':'thread-test','turn':{'id':'turn-test','status':'completed'}}})
 elif m=='turn/interrupt':
  emit({'id':d['id'],'result':{}})
  emit({'method':'turn/completed','params':{'threadId':'thread-test','turn':{'id':'turn-test','status':'interrupted'}}})
''', encoding="utf-8")
    script.chmod(0o700)
    return script, log


def test_native_codex_stdio_and_resumption_disable_unrelated_capabilities(fake_codex):
    script, log = fake_codex
    settings = Settings(codex_path=str(script)).model_dump()
    first = asyncio.run(collect(settings))
    second = asyncio.run(collect(settings, thread_id="thread-test"))
    assert "".join(e.get("text", "") for e in first) == "6 台设备"
    assert first[-1] == {"type": "done"}
    assert second[-1] == {"type": "done"}
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["method"] for r in calls].count("initialize") == 8
    resumed = next(r["params"] for r in calls if r["method"] == "thread/resume")
    assert resumed["threadId"] == "thread-test"
    assert resumed["sandbox"] == "read-only"
    assert resumed["config"]["features.shell_tool"] is False
    assert resumed["config"]["features.plugins"] is False
    assert resumed["config"]["features.code_mode"] is False
    assert resumed["config"]["features.code_mode_host"] is True
    assert resumed["config"]["web_search"] == "disabled"
    servers = resumed["config"]["mcp_servers"]
    assert servers.pop("global_tool") == {"enabled": False}
    assert len(servers)==1 and next(iter(servers)).startswith("iptopo_")
    assert "TEST-MCP-TOKEN" not in log.read_text(encoding="utf-8")


def test_codex_cancel_sends_native_interrupt(fake_codex):
    script, log = fake_codex
    async def run():
        cancel = asyncio.Event()
        with pytest.raises(ProviderCancelled):
            async for event in stream_turn(Settings(codex_path=str(script)).model_dump(), prompt="WAIT", history=[],
                    thread_id=None,mcp_url="http://localhost/mcp",mcp_token="TOKEN",execute_tool=execute_tool,cancel=cancel):
                if event["type"] == "tool":
                    cancel.set()
    asyncio.run(run())
    assert "turn/interrupt" in log.read_text(encoding="utf-8")


def test_closing_stream_interrupts_and_awaits_native_cleanup(fake_codex):
    script,log=fake_codex
    async def run():
        generator=stream_turn(Settings(codex_path=str(script)).model_dump(),prompt="WAIT",history=[],thread_id=None,
            mcp_url="http://localhost/mcp",mcp_token="TOKEN",execute_tool=execute_tool,cancel=asyncio.Event())
        async for event in generator:
            if event["type"]=="tool":break
        await generator.aclose()
        assert "turn/interrupt" in log.read_text(encoding="utf-8")
    asyncio.run(run())


def test_private_codex_profile_copies_only_connection_fields_not_instructions_or_secrets(tmp_path,monkeypatch):
    import tomllib
    native=tmp_path/"native";native.mkdir()
    (native/"auth.json").write_text("AUTH-FIXTURE", encoding="utf-8")
    (native/"AGENTS.md").write_text("GLOBAL-INSTRUCTIONS-FIXTURE", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME",str(native))
    private=tmp_path/"private"
    rpc=providers._CodexRPC("codex",str(tmp_path),private_home=private)
    rpc._prepare_private_home({"model":"model-x","developer_instructions":"GLOBAL-INSTRUCTIONS-FIXTURE",
        "instructions":"GLOBAL-INSTRUCTIONS-FIXTURE","hooks":{"dangerous":"yes"},"mcp_servers":{"shell":{}},
        "model_providers":{"custom":{"name":"Custom","base_url":"http://localhost:8000/v1","env_key":"MODEL_API_KEY",
            "experimental_bearer_token":"SECRET-FIXTURE","http_headers":{"Authorization":"SECRET-FIXTURE"},
            "env_http_headers":{"X-API-Key":"MODEL_API_KEY"}}}})
    text=(private/"config.toml").read_text(encoding="utf-8");config=tomllib.loads(text)
    assert config["model"]=="model-x"
    assert config["developer_instructions"]==providers.INSTRUCTIONS
    assert config["model_providers"]["custom"]["env_key"]=="MODEL_API_KEY"
    assert not any(v in text for v in ["SECRET-FIXTURE","GLOBAL-INSTRUCTIONS-FIXTURE","mcp_servers","hooks"])
    authentication = private / "auth.json"
    # Windows commonly denies symlink creation without Developer Mode. The
    # supported hardlink fallback must still share the native file, never copy
    # credential bytes into an independently owned authentication file.
    assert os.path.samefile(authentication, native / "auth.json")
    if authentication.is_symlink():
        assert authentication.resolve() == native / "auth.json"
    else:
        assert (private / ".native-auth-hardlink").is_file()
        assert authentication.stat().st_nlink >= 2
    assert not (private/"AGENTS.md").exists()


@pytest.mark.parametrize("hardlink_fails",[False,True])
def test_codex_auth_link_handles_missing_symlink_privilege_without_copying(tmp_path,monkeypatch,hardlink_fails):
    native=tmp_path/"native";native.mkdir()
    source=native/"auth.json";source.write_text("AUTH-FIXTURE", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME",str(native))
    def unavailable(*a,**kw):raise PermissionError("No symlink privilege")
    monkeypatch.setattr(Path,"symlink_to",unavailable)
    if hardlink_fails:monkeypatch.setattr(os,"link",unavailable)
    destination=tmp_path/"private"
    rpc=providers._CodexRPC("codex",str(tmp_path),private_home=destination)
    if hardlink_fails:
        with pytest.raises(ProviderError) as error:rpc._prepare_private_home({})
        assert error.value.code=="authentication_link"
        assert "AUTH-FIXTURE" not in str(error.value)
        assert not (destination/"auth.json").exists()
    else:
        rpc._prepare_private_home({})
        assert os.path.samefile(source,destination/"auth.json")
        replacement=native/"replacement";replacement.write_text("NEW-AUTH-FIXTURE", encoding="utf-8");replacement.replace(source)
        assert not os.path.samefile(source,destination/"auth.json")
        rpc._prepare_private_home({})
        assert os.path.samefile(source,destination/"auth.json")


def test_unexpected_native_capability_stops_turn(fake_codex):
    script, log = fake_codex
    with pytest.raises(ProviderError) as error:
        asyncio.run(collect(Settings(codex_path=str(script)).model_dump(), prompt="FORBIDDEN"))
    assert error.value.code == "tool_scope"
    assert "turn/interrupt" in log.read_text(encoding="utf-8")


def test_codex_probe_uses_handshake_without_inference(fake_codex):
    script, log = fake_codex
    result = asyncio.run(probe_backend(Settings(codex_path=str(script)).model_dump()))
    assert result["available"] is True
    assert result["version"] == "codex-cli protocol-fixture"
    assert "turn/start" not in log.read_text(encoding="utf-8")


def test_codex_rpc_request_timeout_cleans_pending_future(fake_codex, tmp_path):
    script, _ = fake_codex
    async def run():
        rpc = providers._CodexRPC(str(script),str(tmp_path))
        try:
            await rpc.start()
            with pytest.raises(asyncio.TimeoutError):
                await rpc.call("not/responded", {}, timeout=.01)
            assert not rpc.pending
        finally:
            await rpc.close()
        assert rpc.process.returncode is not None
    asyncio.run(run())


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda *args, **kwargs: original(*args, **kwargs, transport=httpx.MockTransport(handler)))


def chat_sse(value, *, done=True):
    events = value if isinstance(value,list) else [value]
    return "".join("data: "+json.dumps(e)+"\n\n" for e in events) + ("data: [DONE]\n\n" if done else "")


def test_chat_api_executes_only_registered_tools_and_continues_with_result(monkeypatch):
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert "thinking" not in body
        if len(calls)==1:
            events=[{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"tool-1","function":{"name":"network_","arguments":"{"}}]}}]},
                    {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"summary","arguments":"}"}}]},"finish_reason":"tool_calls"}]}]
        else:
            assert body["messages"][-1]["role"]=="tool"
            assert json.loads(body["messages"][-1]["content"])["data"]["device_count"]==6
            events=[{"choices":[{"delta":{"content":"6 台设备"},"finish_reason":"stop"}]}]
        return httpx.Response(200,text=chat_sse(events))
    mock_http(monkeypatch,handler)
    events=asyncio.run(collect(Settings(backend="openai",model="example",api_url="https://models.example/v1").model_dump()))
    assert "".join(e.get("text","") for e in events)=="6 台设备"
    assert [e["status"] for e in events if e["type"]=="tool"]==["running","completed"]


@pytest.mark.parametrize("base_path", ["", "/v1"])
def test_deepseek_auto_protocol_streaming_tool_loop_and_followup(monkeypatch, base_path):
    calls=[]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "DEEPSEEK-TEST-KEY")
    def handler(request):
        body=json.loads(request.content); calls.append(body)
        assert request.url.host == "api.deepseek.com"
        assert request.url.path == base_path + "/chat/completions"
        assert request.headers["Authorization"] == "Bearer DEEPSEEK-TEST-KEY"
        assert body["model"] == "deepseek-flash"
        assert body["thinking"] == {"type": "disabled"}
        assert body["stream"] and body["tools"][0]["function"]["name"] == "network_summary"
        if len(calls) == 1:
            events=[{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-ds","type":"function","function":{"name":"network_summary","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}]
        else:
            if len(calls) == 2:
                assert body["messages"][-1]["tool_call_id"] == "call-ds"
                assert json.loads(body["messages"][-1]["content"])["data"]["device_count"] == 6
            else:
                assert body["messages"][-2] == {"role":"assistant", "content":"6 台设备"}
            events=[{"choices":[{"delta":{"content":"6 台设备"},"finish_reason":"stop"}]}]
        return httpx.Response(200, text=chat_sse(events))
    mock_http(monkeypatch, handler)
    config=Settings(backend="openai", model="deepseek-flash", api_url="https://api.deepseek.com"+base_path,
                    api_key_env="DEEPSEEK_API_KEY").model_dump()
    first=asyncio.run(collect(config))
    followup=asyncio.run(collect(config, history=[{"role":"assistant", "content":"6 台设备"}]))
    assert first[-1] == followup[-1] == {"type":"done"}
    assert "".join(e.get("text", "") for e in first) == "6 台设备"
    assert len(calls) == 3


@pytest.mark.parametrize("name,arguments,code",[("shell","{}","tool_scope"),("network_summary","[]","tool_arguments"),("network_summary","broken","tool_arguments")])
def test_chat_api_invalid_tools_or_arguments_never_reach_executor(monkeypatch,name,arguments,code):
    def handler(request):
        return httpx.Response(200,text=chat_sse({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":name,"arguments":arguments}}]},"finish_reason":"tool_calls"}]}))
    mock_http(monkeypatch,handler)
    async def forbidden(*args):
        pytest.fail("Invalid tool invocation reached application executor")
    with pytest.raises(ProviderError) as error:
        asyncio.run(collect(Settings(backend="openai",model="example",api_protocol="chat_completions").model_dump(),execute_tool=forbidden))
    assert error.value.code==code


def test_chat_truncated_stream_is_not_marked_complete(monkeypatch):
    mock_http(monkeypatch,lambda request:httpx.Response(200,text=chat_sse({"choices":[{"delta":{"content":"partial"}}]},done=False)))
    with pytest.raises(ProviderError) as error:
        asyncio.run(collect(Settings(backend="openai",model="example",api_protocol="chat_completions").model_dump()))
    assert error.value.code=="model_protocol"


def test_provider_http_error_does_not_expose_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","PRIVATE-SECRET")
    mock_http(monkeypatch,lambda request:httpx.Response(401,json={"error":"PRIVATE-SECRET"}))
    with pytest.raises(ProviderError) as error:
        asyncio.run(collect(Settings(backend="openai",model="example").model_dump()))
    assert "PRIVATE-SECRET" not in str(error.value)


def test_responses_api_keeps_reasoning_items_in_tool_continuation(monkeypatch):
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        assert request.url.path=="/v1/responses"
        assert body["store"] is False
        if len(calls)==1:
            output=[{"type":"reasoning","id":"reason-1","encrypted_content":"opaque","summary":[]},
                    {"type":"function_call","call_id":"call-1","name":"network_summary","arguments":"{}"}]
            events=[{"type":"response.completed","response":{"status":"completed","output":output}}]
        else:
            assert any(i.get("type")=="reasoning" and i["encrypted_content"]=="opaque" for i in body["input"])
            assert body["input"][-1]["type"]=="function_call_output"
            events=[{"type":"response.output_text.delta","delta":"6 台设备"},
                    {"type":"response.completed","response":{"status":"completed","output":[],"usage":{"input_tokens":100}}}]
        return httpx.Response(200,text=chat_sse(events,done=False))
    mock_http(monkeypatch,handler)
    events=asyncio.run(collect(Settings(backend="openai",model="example").model_dump()))
    assert "".join(e.get("text","") for e in events)=="6 台设备"
    assert events[-1]=={"type":"done"}
    assert "opaque" not in json.dumps(events)


class LiveFixture(httpx.AsyncByteStream):
    def __init__(self,started,emitted):
        self.started,self.emitted=started,emitted
    async def __aiter__(self):
        await self.started.wait()
        yield ("data: "+json.dumps({"type":"message.part.updated","properties":{"part":{"id":"user-part","sessionID":"ses-test","messageID":"user-message","type":"text","text":"DO NOT STREAM USER"}}})+"\n\n").encode()
        yield ("data: "+json.dumps({"type":"message.updated","properties":{"info":{"sessionID":"ses-test","id":"msg-1","role":"assistant"}}})+"\n\n").encode()
        event={"type":"message.part.updated","properties":{"part":{"id":"part-1","messageID":"msg-1","sessionID":"ses-test","type":"text","text":"6 台设备"}}}
        yield ("data: "+json.dumps(event)+"\n\n").encode()
        self.emitted.set()
        await asyncio.Event().wait()


def test_opencode_v1_native_session_permission_and_events(monkeypatch):
    async def run():
        started,emitted=asyncio.Event(),asyncio.Event()
        calls=[]
        async def handler(request):
            path=request.url.path;body=json.loads(request.content) if request.content else {}
            calls.append((request.method,path,body))
            if path=="/agent":return httpx.Response(200,json=private_agents())
            if path=="/session":
                return httpx.Response(200,json={"id":"ses-test","permission":body["permission"]})
            if path=="/mcp":
                assert body["config"]["headers"]["Authorization"]=="Bearer TEST-MCP-TOKEN"
                return httpx.Response(200,json={body["name"]:{"status":"connected"}})
            if path=="/event":
                return httpx.Response(200,stream=LiveFixture(started,emitted))
            if path=="/session/ses-test/message":
                started.set();await emitted.wait();await asyncio.sleep(.01)
                return httpx.Response(200,json={"info":{"role":"assistant"},"parts":[{"type":"text","text":"6 台设备"}]})
            return httpx.Response(200,json=True)
        mock_http(monkeypatch,handler)
        events=await collect(Settings(backend="opencode").model_dump())
        assert "".join(e.get("text","") for e in events)=="6 台设备"
        assert events[-1]=={"type":"done"}
        create=next(body for method,path,body in calls if path=="/session")
        assert create["permission"][0]=={"permission":"*","pattern":"*","action":"deny"}
        prompt=next(body for method,path,body in calls if path.endswith("/message"))
        assert prompt["agent"]=="iptopo" and prompt["system"]==providers.INSTRUCTIONS
        assert not any(path.endswith("/abort") for _,path,_ in calls)
    asyncio.run(run())


@pytest.mark.parametrize("version",["v1","v2"])
def test_opencode_refuses_servers_that_ignore_session_permission(monkeypatch,version):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/agent"):
            agents=private_agents(version)
            return httpx.Response(200,json={"data":agents} if version=="v2" else agents)
        value={"id":"ses-test"}
        return httpx.Response(200,json={"data":value} if version=="v2" else value)
    mock_http(monkeypatch,handler)
    with pytest.raises(ProviderError) as error:
        asyncio.run(collect(Settings(backend="opencode",opencode_api=version).model_dump()))
    assert error.value.code=="agent_permissions"
    assert not any(path.endswith("/message") or path.endswith("/prompt") for path in calls)


def test_opencode_cancel_calls_native_abort(monkeypatch):
    async def run():
        started,emitted=asyncio.Event(),asyncio.Event()
        cancel=asyncio.Event();calls=[]
        async def handler(request):
            path=request.url.path;body=json.loads(request.content) if request.content else {}
            calls.append(path)
            if path=="/agent":return httpx.Response(200,json=private_agents())
            if path=="/session":return httpx.Response(200,json={"id":"ses-test","permission":body["permission"]})
            if path=="/mcp":return httpx.Response(200,json={body["name"]:{"status":"connected"}})
            if path=="/event":return httpx.Response(200,stream=LiveFixture(started,emitted))
            if path.endswith("/message"):
                started.set();await emitted.wait();cancel.set();await asyncio.Event().wait()
            return httpx.Response(200,json=True)
        mock_http(monkeypatch,handler)
        with pytest.raises(ProviderCancelled):
            await collect(Settings(backend="opencode").model_dump(),cancel=cancel)
        assert "/session/ses-test/abort" in calls
    asyncio.run(run())


@pytest.mark.parametrize("version",["v1","v2"])
@pytest.mark.parametrize("failure",["missing","broad","shell","native"])
def test_opencode_requires_private_agent_before_any_session_or_mcp(monkeypatch,version,failure):
    agents=private_agents(version)
    field="permissions" if version=="v2" else "permission"
    if failure=="missing":agents=[]
    elif failure=="broad":agents[0][field]=[]
    elif failure=="native":agents[0]["native"]=True
    else:
        agents[0][field].append({"action":"shell","resource":"*","effect":"allow"} if version=="v2" else {"permission":"bash","pattern":"*","action":"allow"})
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json={"data":agents} if version=="v2" else agents)
    mock_http(monkeypatch,handler)
    with pytest.raises(ProviderError) as error:
        asyncio.run(collect(Settings(backend="opencode",opencode_api=version).model_dump()))
    assert error.value.code=="agent_permissions"
    assert calls==[("GET","/api/agent" if version=="v2" else "/agent")]


class V2EventFixture(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"type":"server.connected"}\n\n'
        await asyncio.Event().wait()


@pytest.mark.parametrize("stop",[False,True])
def test_opencode_v2_native_model_mcp_completion_and_interrupt(monkeypatch,stop):
    async def run():
        state={"server":None,"permissions":None,"prompted":False}
        calls=[];cancel=asyncio.Event()
        async def handler(request):
            path=request.url.path;body=json.loads(request.content) if request.content else {}
            calls.append((request.method,path,body))
            if path=="/api/agent":return httpx.Response(200,json={"data":private_agents("v2")})
            if path=="/api/session":
                assert body["agent"]=="iptopo"
                assert body["model"]=={"providerID":"example","model":"model-v2"}
                state["permissions"]=body["permissions"]
                return httpx.Response(200,json={"data":{"id":"ses-test","permissions":body["permissions"]}})
            if path.startswith("/api/experimental/mcp/") and request.method=="PUT":
                state["server"]=path.rsplit("/",1)[1]
                assert body["config"]["protocol"]=="legacy"
                assert body["config"]["codemode"] is False
                assert "enabled" not in body["config"]
                return httpx.Response(204)
            if path=="/api/mcp":
                return httpx.Response(200,json={"data":[{"name":state["server"],"status":{"status":"connected"}}]})
            if path=="/api/event":return httpx.Response(200,stream=V2EventFixture())
            if path.endswith("/prompt"):
                assert set(body)=={"text"};state["prompted"]=True
                return httpx.Response(204)
            if path=="/api/experimental/session/ses-test/wait":
                if stop:await asyncio.Event().wait()
                return httpx.Response(204)
            if path=="/api/session/ses-test/message":
                values=[] if not state["prompted"] else [{"id":"message-current","type":"assistant","finish":"stop","content":[{"type":"text","text":"6 台设备"}]}]
                return httpx.Response(200,json={"data":values})
            return httpx.Response(204)
        mock_http(monkeypatch,handler)
        events=[]
        try:
            async for event in stream_turn(Settings(backend="opencode",opencode_api="v2",model="example/model-v2").model_dump(),prompt="设备数量",history=[],thread_id=None,mcp_url="http://localhost/mcp",mcp_token="TOKEN",execute_tool=execute_tool,cancel=cancel):
                events.append(event)
                if stop and event["type"]=="delta":cancel.set()
        except ProviderCancelled:
            assert stop
        assert "".join(e.get("text","") for e in events)=="6 台设备"
        paths=[path for _,path,_ in calls]
        if stop:
            assert "/api/session/ses-test/interrupt" in paths
            assert not any(e["type"]=="done" for e in events)
        else:
            assert events[-1]=={"type":"done"}
            assert "/api/session/ses-test/interrupt" not in paths
        assert any(method=="DELETE" and path.startswith("/api/experimental/mcp/") for method,path,_ in calls)
    asyncio.run(run())


def test_unconfigured_api_probe_never_sends_model_request(monkeypatch):
    def forbidden(request):pytest.fail("Empty model should not contact any API")
    mock_http(monkeypatch,forbidden)
    result=asyncio.run(probe_backend(Settings().model_dump(),"openai"))
    assert result["available"] is False and "模型名称" in result["detail"]
