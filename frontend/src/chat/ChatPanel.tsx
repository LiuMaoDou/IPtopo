import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, CheckCircle2, History, MessageSquare, Plus, Send, Settings, Square, Trash2, X } from "lucide-react";
import { api } from "../types";
import AgentSettings from "./AgentSettings";
import ResultCard from "./ResultCard";
import { contextIsCurrent, mergeCards, readEventStream } from "./stream";
import { agentLabels, defaultAISettings, type AgentBackend, type AgentStatus, type ChatContext, type ChatMessage, type ChatPanelProps, type ChatSession, type AISettings } from "./types";

const toolNames: Record<string, string> = { network_summary: "读取项目概况", search_devices: "查找设备", device_details: "读取设备信息", query_topology: "查询组网关系", list_path_targets: "查询路径计算范围", config_path: "计算配置路径", lsdb_path: "计算 LSDB 路径", compare: "对比配置与 LSDB", get_evidence: "读取来源证据", get_project_summary: "读取项目概况", get_device: "读取设备信息", get_device_details: "读取设备信息", get_topology: "查询组网关系", get_neighbors: "查询相邻设备", simulate_path: "计算配置路径", calculate_path: "计算路径", analyze_lsdb: "分析 LSDB", get_diagnostics: "检查数据诊断", get_source_evidence: "读取来源证据", query_project: "查询项目数据" };
const messageStates = { completed: "已完成", running: "正在回答", cancelled: "已停止", error: "未完成" };
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function ChatPanel(props: ChatPanelProps) {
  const { project, selection, revision, canvasMode, lsdbBatchId, importing } = props;
  const callbacks = useRef(props); callbacks.current = props;
  const [settings, setSettings] = useState<AISettings>(defaultAISettings);
  const [statuses, setStatuses] = useState<AgentStatus[]>([]);
  const [backend, setBackend] = useState<AgentBackend>("codex");
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [messagesTruncated, setMessagesTruncated] = useState(false);
  const [context, setContext] = useState<ChatContext>();
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [toolPhase, setToolPhase] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [useSelection, setUseSelection] = useState(true);
  const generation = useRef(0);
  const historyGeneration = useRef(0);
  const skipHistory = useRef("");
  const transport = useRef<{ controller: AbortController; runId: string; assistantId: string } | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const activeSession = sessions.find(item => item.id === sessionId);
  const activeBackend = activeSession?.backend || backend;
  const status = statuses.find(item => (item.id || item.backend) === activeBackend);
  const deviceNames = useMemo(() => new Map<string, string>([...(project.snapshot?.devices.map(device => [device.id, device.hostname] as [string, string]) || []), ...(project.snapshot?.interfaces.map(iface => [iface.id, iface.raw_name] as [string, string]) || [])]), [project.snapshot?.devices, project.snapshot?.interfaces]);
  const selectedLabel = selection ? deviceNames.get(selection.id) || project.snapshot?.interfaces.find(item => item.id === selection.id)?.raw_name || ({ device: "选中设备", interface: "选中接口", link: "选中链路", segment: "选中网段", bundle: "选中链路组" }[selection.kind]) : "";
  const fallbackContext: ChatContext = { project_id: project.id, snapshot_id: project.snapshot_id || null, revision: project.effective_revision };
  const currentContext = project.effective_revision ? fallbackContext : context;

  const cancelTransport = () => {
    const active = transport.current;
    transport.current = null;
    generation.current++;
    active?.controller.abort();
    if (active?.runId) void api(`/api/projects/${project.id}/chat/runs/${active.runId}/cancel`, { method: "POST", body: "{}" }).catch(() => {});
    return active;
  };

  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const options = { signal: controller.signal };
    void Promise.allSettled([
      api<AISettings>("/api/ai/settings", options),
      api<{ backends: AgentStatus[] }>("/api/ai/status", options),
      api<{ sessions: ChatSession[]; current_context?: ChatContext }>(`/api/projects/${project.id}/chat/sessions`, options),
    ]).then(([configuration, availability, history]) => {
      if (!active) return;
      if (configuration.status === "fulfilled") { setSettings({ ...defaultAISettings, ...configuration.value }); setBackend(configuration.value.backend); }
      if (availability.status === "fulfilled") setStatuses(availability.value.backends || []);
      if (history.status === "fulfilled") { setSessions(history.value.sessions); if (history.value.current_context) setContext(history.value.current_context); }
      const failed = [configuration, availability, history].find(item => item.status === "rejected");
      if (failed?.status === "rejected") setError(errorText(failed.reason));
      setLoading(false);
    });
    return () => { active = false; controller.abort(); cancelTransport(); };
  }, [project.id]);

  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setContext(undefined);
    void api<ChatContext>(`/api/projects/${project.id}/chat/context`, { signal: controller.signal })
      .then(value => { if (active) setContext(value); })
      .catch(() => {});
    // A running answer is tied to the facts captured at submission time.
    const previous = cancelTransport();
    if (previous) {
      setMessages(items => items.map(message => message.id === previous.assistantId && message.status === "running" ? { ...message, status: "cancelled" } : message));
      setRunning(false); setToolPhase("");
    }
    return () => { active = false; controller.abort(); };
  }, [project.id, revision]);

  useEffect(() => { setUseSelection(true); }, [selection?.id]);

  useEffect(() => {
    const token = ++historyGeneration.current;
    if (skipHistory.current === sessionId) { skipHistory.current = ""; return; }
    setMessages([]); setMessagesTruncated(false); setError(""); callbacks.current.onClearHighlight();
    if (!sessionId) { setHistoryLoading(false); return; }
    const controller = new AbortController(); setHistoryLoading(true);
    void api<{ session: ChatSession; messages: ChatMessage[]; current_context?: ChatContext; messages_truncated?: boolean }>(`/api/projects/${project.id}/chat/sessions/${sessionId}`, { signal: controller.signal })
      .then(value => {
        if (token !== historyGeneration.current) return;
        setMessages(value.messages); setMessagesTruncated(Boolean(value.messages_truncated)); setBackend(value.session.backend); if (value.current_context) setContext(value.current_context);
      }).catch(failure => { if (!controller.signal.aborted && token === historyGeneration.current) setError(errorText(failure)); })
      .finally(() => { if (token === historyGeneration.current) setHistoryLoading(false); });
    return () => controller.abort();
  }, [project.id, sessionId]);

  useEffect(() => { if (stickToBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [messages, toolPhase]);

  const refreshSessions = async (signal?: AbortSignal) => {
    const result = await api<{ sessions: ChatSession[] }>(`/api/projects/${project.id}/chat/sessions`, { signal });
    if (!signal?.aborted) setSessions(result.sessions);
  };
  const createSession = async (agent = activeBackend, signal?: AbortSignal) => {
    const value = await api<ChatSession>(`/api/projects/${project.id}/chat/sessions`, { method: "POST", signal, body: JSON.stringify({ backend: agent, ...(settings.model ? { model: settings.model } : {}) }) });
    setSessions(items => [value, ...items.filter(item => item.id !== value.id)]);
    return value;
  };
  const newConversation = (agent = activeBackend) => {
    cancelTransport(); setSessionId(""); setBackend(agent); setMessages([]); setMessagesTruncated(false); setError(""); setToolPhase(""); setRunning(false); callbacks.current.onClearHighlight();
  };
  const deleteSession = async () => {
    if (!sessionId || running) return;
    try { await api(`/api/projects/${project.id}/chat/sessions/${sessionId}`, { method: "DELETE" }); setSessions(items => items.filter(item => item.id !== sessionId)); newConversation(); }
    catch (failure) { setError(errorText(failure)); }
  };
  const stop = () => {
    const active = cancelTransport(); setRunning(false); setToolPhase("");
    if (active) setMessages(items => items.map(message => message.id === active.assistantId && message.status === "running" ? { ...message, status: "cancelled" } : message));
  };
  const send = async (event?: React.FormEvent, prompt = draft) => {
    event?.preventDefault(); const text = prompt.trim();
    if (!text || running || loading || historyLoading || !status?.available) return;
    setError(""); setDraft(""); setRunning(true); setToolPhase("准备查询当前项目…"); stickToBottom.current = true;
    const controller = new AbortController(); const token = ++generation.current;
    const placeholderId = `assistant-${crypto.randomUUID()}`;
    const active = { controller, runId: "", assistantId: placeholderId }; transport.current = active;
    const messageContext = currentContext || fallbackContext;
    const timestamp = new Date().toISOString();
    const userMessage: ChatMessage = { id: `user-${crypto.randomUUID()}`, role: "user", content: text, status: "completed", context: messageContext, cards: [], created_at: timestamp };
    const placeholder: ChatMessage = { id: placeholderId, role: "assistant", content: "", status: "running", context: messageContext, cards: [], created_at: timestamp };
    const updateAssistant = (transform: (message: ChatMessage) => ChatMessage) => setMessages(items => items.map(message => message.id === active.assistantId ? transform(message) : message));
    let terminal = false;
    setMessages(items => [...items, userMessage, placeholder]);
    try {
      let selectedSession = activeSession;
      if (!selectedSession) {
        selectedSession = await createSession(activeBackend, controller.signal);
        if (generation.current !== token || controller.signal.aborted) return;
        skipHistory.current = selectedSession.id; setSessionId(selectedSession.id);
      }
      const response = await fetch(`/api/projects/${project.id}/chat/sessions/${selectedSession.id}/messages`, {
        method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ text, ...(useSelection && selection ? { selection: { kind: selection.kind, id: selection.id } } : {}), canvas_mode: canvasMode, ...(canvasMode === "lsdb" && lsdbBatchId ? { lsdb_batch_id: lsdbBatchId } : {}) }),
      });
      await readEventStream(response, value => {
        if (generation.current !== token || controller.signal.aborted) return;
        if (value.type === "start") {
          active.runId = value.run_id;
          const oldId = active.assistantId; active.assistantId = value.message.id;
          setMessages(items => items.map(message => message.id === oldId ? { ...value.message, context: value.context || value.message.context, cards: value.message.cards || [] } : message));
          setToolPhase("Agent 正在查询…");
        } else if (value.type === "delta") {
          updateAssistant(message => ({ ...message, content: message.content + value.text }));
          setToolPhase("正在组织回答…");
        } else if (value.type === "tool") {
          setToolPhase(`${toolNames[value.name] || "查询项目数据"}${["done", "completed", "success", "ok"].includes(value.status) ? " · 已完成" : "…"}`);
        } else if (value.type === "cards") {
          updateAssistant(message => ({ ...message, cards: mergeCards(message.cards || [], value.cards || []) }));
        } else if (value.type === "done") {
          terminal = true;
          updateAssistant(message => ({ ...message, ...value.message, cards: value.message.cards || message.cards }));
        } else if (value.type === "error") {
          terminal = true; setError(value.message);
          updateAssistant(message => ({ ...message, status: "error", error: value.message }));
        }
      }, controller.signal);
      if (!terminal && generation.current === token) throw new Error("连接在回答完成前中断，请重试。");
      if (generation.current === token) void refreshSessions(controller.signal).catch(() => {});
    } catch (failure) {
      if (generation.current === token && !controller.signal.aborted) {
        const detail = errorText(failure); setError(detail);
        updateAssistant(message => ({ ...message, status: "error", error: detail }));
      }
    } finally {
      if (generation.current === token) { transport.current = null; setRunning(false); setToolPhase(""); }
    }
  };
  const updateStatus = (value: AgentStatus) => setStatuses(items => [...items.filter(item => (item.id || item.backend) !== (value.id || value.backend)), value]);
  const configured = (value: AISettings) => { setSettings(value); setStatuses(items => items.filter(item => (item.id || item.backend) !== value.backend)); if (!sessionId) setBackend(value.backend); };

  return <aside className="inspector chat-panel" aria-label="AI 对话面板">
    <div className="chat-heading"><div className="chat-heading-icon"><MessageSquare size={20} /></div><div><h2>AI 对话</h2><small>基于项目事实与可追溯证据</small></div><button aria-label="AI Agent 设置" title="Agent 设置" onClick={() => setSettingsOpen(true)}><Settings size={16} /></button><button aria-label="关闭 AI 对话" onClick={props.onClose}><X size={17} /></button></div>
    <div className="chat-controls"><label><span className="sr-only">选择 AI Agent</span><select aria-label="选择 AI Agent" value={activeBackend} disabled={running} onChange={event => newConversation(event.target.value as AgentBackend)}>{Object.entries(agentLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><button title="新建对话" aria-label="新建 AI 对话" disabled={running} onClick={() => newConversation()}><Plus size={15} /></button><button title="清除画布高亮" aria-label="清除 AI 高亮" onClick={props.onClearHighlight}><X size={14} />高亮</button></div>
    <div className={`chat-availability ${status?.available ? "ready" : "pending"}`}><span>{status?.available ? <CheckCircle2 size={12} /> : <Bot size={12} />}{loading ? "正在读取 Agent 状态…" : status ? status.available ? "Agent 已就绪" : "Agent 未就绪" : "Agent 尚未检测"}</span><button className="text-button" onClick={() => setSettingsOpen(true)}>{status?.available ? "设置" : "配置与检测"}</button>{status?.detail && !status.available && <small>{status.detail}</small>}</div>
    <div className="chat-history"><History size={14} /><select aria-label="AI 会话历史" disabled={running || loading} value={sessionId} onChange={event => setSessionId(event.target.value)}><option value="">新对话</option>{sessions.map(session => <option value={session.id} key={session.id}>{session.title || "未命名对话"} · {agentLabels[session.backend] || session.backend}</option>)}</select><button disabled={!sessionId || running} aria-label="删除当前 AI 会话" title="删除当前会话" onClick={() => void deleteSession()}><Trash2 size={14} /></button></div>
    <div className="chat-context"><span title={project.name}>项目 · {project.name}</span><span>{canvasMode === "lsdb" ? "LSDB 观测" : "配置事实"}</span>{selection && useSelection && <button title="移除本轮选中对象上下文" onClick={() => setUseSelection(false)}>{selectedLabel}<X size={11} /></button>}{importing && <span>导入中 · 使用已保存快照</span>}</div>
    <div className="chat-messages" ref={scroll} onScroll={() => { const element = scroll.current; if (element) stickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 90; }}>
      {messagesTruncated && <p className="chat-card-note">仅显示此会话最近 500 条消息。</p>}
      {loading || historyLoading ? <div className="chat-empty" role="status"><span className="spinner" />正在读取对话…</div> : messages.length === 0 ? <div className="chat-empty"><div className="chat-empty-icon"><Bot size={28} /></div><h3>从一个网络问题开始</h3><p>查询设备、接口、组网关系和路径，回答附带项目证据。</p><div className="chat-starters">{["这个项目的组网情况如何？", "列出设备及其三层接口", "查询路径前需要哪些信息？"].map(prompt => <button key={prompt} disabled={!status?.available} onClick={() => void send(undefined, prompt)}>{prompt}</button>)}</div></div> : messages.map(message => <article key={message.id} className={`chat-message ${message.role}`} aria-label={message.role === "user" ? "你的问题" : "Agent 回答"}><div className="chat-message-label"><b>{message.role === "user" ? "你" : agentLabels[activeBackend]}</b>{message.role === "assistant" && <small>{messageStates[message.status] || message.status}</small>}</div>{message.content && <div className="chat-message-content">{message.content}</div>}{!message.content && message.status === "running" && <div className="chat-thinking"><span className="spinner" />正在查询…</div>}{(message.cards || []).map(card => <ResultCard key={card.id} card={card} context={message.context} current={contextIsCurrent(message.context, currentContext)} onSource={props.onSource} onAction={props.onAction} deviceNames={deviceNames} />)}{message.status === "cancelled" && <p className="chat-message-note">本轮已停止，以上内容可能尚未完整。</p>}{message.error && <p className="chat-error">{message.error}</p>}</article>)}
    </div>
    <div className="chat-composer-wrap">{error && <p className="chat-error" role="alert">{error}</p>}{running && <p className="chat-tool-phase" role="status"><span className="spinner" />{toolPhase}</p>}<form className="chat-composer" onSubmit={event => void send(event)}><textarea aria-label="向 AI 提问" value={draft} onChange={event => setDraft(event.target.value)} rows={3} maxLength={8000} placeholder={status?.available ? "询问设备、组网或路径…" : "请先配置并检测 AI Agent"} disabled={loading || historyLoading || running || !status?.available} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />{running ? <button type="button" className="chat-stop" onClick={stop}><Square size={13} />停止</button> : <button type="submit" className="primary" disabled={!draft.trim() || loading || historyLoading || !status?.available}><Send size={14} />发送</button>}</form><p className="chat-disclaimer">仅查询当前项目 · 路径由引擎计算 · 配置与观测均不代表实时可达</p></div>
    {settingsOpen && <AgentSettings settings={settings} projectId={project.id} onSaved={configured} onStatus={updateStatus} onClose={() => setSettingsOpen(false)} />}
  </aside>;
}
