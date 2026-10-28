import { useState } from "react";
import { CheckCircle2, Copy, Plug, RefreshCw, Save } from "lucide-react";
import { Modal } from "../Dialogs";
import { api } from "../types";
import { agentLabels, type AgentBackend, type AgentStatus, type AISettings } from "./types";

type MCPConnection = { url: string; token: string; expires_at: string; instructions: string; config: Record<string, unknown> };

export default function AgentSettings({ settings, projectId, onSaved, onStatus, onClose }: {
  settings: AISettings; projectId: string; onSaved: (settings: AISettings) => void; onStatus: (status: AgentStatus) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [ttl, setTtl] = useState(30);
  const [connection, setConnection] = useState<MCPConnection | null>(null);
  const update = <K extends keyof AISettings>(key: K, value: AISettings[K]) => { setDraft(previous => ({ ...previous, [key]: value })); setStatus(null); setNotice(""); };
  const save = async (event?: React.FormEvent) => {
    event?.preventDefault(); setSaving(true); setError(""); setNotice("");
    try {
      const value = await api<AISettings>("/api/ai/settings", { method: "PUT", body: JSON.stringify(draft) });
      setDraft(value); onSaved(value); setNotice("配置已保存。点击检测确认 Agent 是否就绪。");
      return value;
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); return null; }
    finally { setSaving(false); }
  };
  const probe = async () => {
    setProbing(true); setError("");
    try {
      const saved = await save();
      if (!saved) return;
      const value = await api<AgentStatus | { backends: AgentStatus[] }>("/api/ai/probe", { method: "POST", body: JSON.stringify({ backend: saved.backend }) });
      const result = "backends" in value ? value.backends.find(item => (item.id || item.backend) === saved.backend) : value;
      if (!result) throw new Error("检测没有返回当前 Agent 的结果");
      setStatus(result); onStatus({ ...result, id: saved.backend }); setNotice("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setProbing(false); }
  };
  const connect = async () => {
    setConnecting(true); setError(""); setConnection(null);
    try { setConnection(await api<MCPConnection>(`/api/projects/${projectId}/chat/mcp-token`, { method: "POST", body: JSON.stringify({ ttl_minutes: ttl }) })); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setConnecting(false); }
  };
  const copyConnection = async () => {
    if (!connection) return;
    try { await navigator.clipboard.writeText(JSON.stringify(connection.config, null, 2)); setNotice("连接配置已复制。请另将临时令牌设置为 IPTOPO_MCP_TOKEN 环境变量。"); }
    catch { setNotice("浏览器无法自动复制，请选中下方配置文本复制。"); }
  };
  const copyToken = async () => {
    if (!connection) return;
    try { await navigator.clipboard.writeText(connection.token); setNotice("临时令牌已复制，请设置到外部 Agent 的 IPTOPO_MCP_TOKEN 环境变量。"); }
    catch { setNotice("浏览器无法复制令牌，请在允许剪贴板访问后重试。"); }
  };
  return <Modal title="AI Agent 设置" onClose={onClose}>
    <form className="chat-settings" onSubmit={event => void save(event)}>
      <p className="chat-help">Agent 通过只读查询工具获取当前项目事实，路径由 IPtopo 计算。配置、证据片段和对话可能发送到所选模型服务。</p>
      <label>默认 Agent<select aria-label="默认 AI Agent" value={draft.backend} onChange={event => update("backend", event.target.value as AgentBackend)}>{Object.entries(agentLabels).map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label>
      <label>模型<input aria-label="AI 模型" value={draft.model} onChange={event => update("model", event.target.value)} placeholder={draft.backend === "openai" ? "填写服务支持的模型 ID" : "留空使用 Agent 默认模型"} /></label>
      <p className="chat-help">更换模型或默认 Agent 后，请新建对话。已有会话保留其 Agent 与模型。</p>
      {draft.backend === "codex" && <>
        <label>Codex 可执行文件<input aria-label="Codex 可执行文件" value={draft.codex_path} onChange={event => update("codex_path", event.target.value)} required placeholder="codex 或绝对路径" /></label>
        <p className="chat-help">使用运行 IPtopo 的电脑上已安装、已登录的 Codex CLI。这里只填写可执行文件路径。</p>
      </>}
      {draft.backend === "opencode" && <>
        <label>OpenCode 服务地址<input aria-label="OpenCode 服务地址" type="url" value={draft.opencode_url} onChange={event => update("opencode_url", event.target.value)} required /></label>
        <label>服务 API 版本<select aria-label="OpenCode API 版本" value={draft.opencode_api} onChange={event => update("opencode_api", event.target.value as "v1" | "v2")}><option value="v1">v1</option><option value="v2">v2</option></select></label>
        <label>服务口令环境变量名<input aria-label="OpenCode 口令环境变量" value={draft.opencode_password_env} onChange={event => update("opencode_password_env", event.target.value)} pattern="[A-Za-z_][A-Za-z0-9_]*" placeholder="OPENCODE_SERVER_PASSWORD" /></label>
        <p className="chat-help">连接你已运行的 OpenCode 服务；服务端从指定环境变量读取口令。</p>
      </>}
      {draft.backend === "openai" && <>
        <label>API 地址<input aria-label="OpenAI API 地址" type="url" value={draft.api_url} onChange={event => update("api_url", event.target.value)} required /></label>
        <label>API 协议<select aria-label="AI API 协议" value={draft.api_protocol} onChange={event => update("api_protocol", event.target.value as AISettings["api_protocol"])}><option value="auto">自动选择</option><option value="responses">Responses</option><option value="chat_completions">Chat Completions</option></select></label>
        <label>API Key 环境变量名<input aria-label="API Key 环境变量" value={draft.api_key_env} onChange={event => update("api_key_env", event.target.value)} pattern="[A-Za-z_][A-Za-z0-9_]*" required /></label>
        <p className="chat-help">填环境变量名，无需在网页输入密钥。自建兼容服务需通过检测确认模型和工具调用能力。</p>
      </>}
      <label>单轮超时（秒）<input aria-label="AI 超时秒数" type="number" min={15} max={600} value={draft.timeout_seconds} onChange={event => update("timeout_seconds", Number(event.target.value))} required /></label>
      {error && <p className="chat-error" role="alert">{error}</p>}
      {notice && <p className="chat-help" role="status">{notice}</p>}
      {status && <div className={`chat-agent-result ${status.available ? "available" : "unavailable"}`} role="status"><b>{status.available ? <CheckCircle2 size={15} /> : <Plug size={15} />}{status.available ? "检测通过" : "Agent 未就绪"}</b><p>{status.detail}</p>{status.version && <small>{status.version}</small>}</div>}
      <div className="chat-settings-actions"><button type="button" disabled={probing || saving} onClick={() => void probe()}><RefreshCw size={14} />{probing ? "正在检测…" : "保存并检测"}</button><button type="submit" className="primary" disabled={saving || probing}><Save size={14} />{saving ? "保存中…" : "保存配置"}</button></div>
    </form>
    <details className="chat-mcp-connect"><summary>连接外部 Codex / OpenCode</summary><p className="chat-help">生成只读 MCP 连接，范围仅限当前项目。临时令牌到期失效。</p><label>有效期（分钟）<input type="number" min={1} max={1440} value={ttl} onChange={event => setTtl(Number(event.target.value))} /></label><button disabled={connecting || !Number.isInteger(ttl) || ttl < 1 || ttl > 1440} onClick={() => void connect()}>{connecting ? "正在生成…" : "生成临时连接配置"}</button>{connection && <><p className="chat-help">到期：{new Date(connection.expires_at).toLocaleString()}</p><p className="chat-help">{connection.instructions}</p><label>IPTOPO_MCP_TOKEN 临时令牌<input aria-label="IPTOPO_MCP_TOKEN 临时令牌" type="password" readOnly value={connection.token} autoComplete="off" /></label><button onClick={() => void copyToken()}><Copy size={13} />复制令牌</button><textarea aria-label="外部 Agent 临时连接配置" rows={9} readOnly value={JSON.stringify(connection.config, null, 2)} /><button onClick={() => void copyConnection()}><Copy size={13} />复制连接配置</button></>}</details>
  </Modal>;
}
