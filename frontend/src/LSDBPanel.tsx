import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Database, Play, Upload, X } from "lucide-react";
import { api, type Project, type Ref, type GraphData, type Selection } from "./types";
import type { SimulationHighlight } from "./routingTypes";
import { lsdbStatus, type LSDBAnalysis, type LSDBBatch } from "./lsdbTypes";

type Props = { project: Project; selection: Selection; busy: boolean; revision: string;
  onBatch?: (id: string) => void;
  onGraph: (graph: GraphData | null, highlight: SimulationHighlight | null) => void;
  onVersion: (version: number) => void; onSource: (ref: Ref) => void; onClose: () => void; onReveal: () => void;
};
const percent = (n: number) => `${Number(n.toFixed(2))}%`;
const vendors = { auto: "自动识别", huawei: "Huawei VRP", cisco: "Cisco IOS / XE / XR", juniper: "Juniper Junos", nokia: "Nokia SR OS" };
type Vendor = keyof typeof vendors;
const captureHints = {
  auto: ["选择厂商后可查看采集命令。自动识别需保留原生数据库标题。", "保留 System ID；动态主机名可配合名称映射表或 LSP 内的 IPv4 地址关联配置。"],
  huawei: ["display ospf <进程> lsdb router / network：需完整详细输出。", "display isis <进程> lsdb verbose no-name（若支持 no-name）。"],
  cisco: ["IOS / XE：show ip ospf <进程> database router / network；XR：show ospf <进程> database router / network。", "show isis [进程] database detail / verbose，保留 Tag / Level 标题。"],
  juniper: ["show ospf database extensive；默认实例填 default。", "show isis database extensive；默认实例填 default，可同时上传 hostname 表。"],
  nokia: ["show router ospf <实例> database detail，保留 Area 和 LSA 详细字段。", "show router isis <实例> database detail；默认实例填 0，可同时上传 hostname 表。"],
};
export default function LSDBPanel(props: Props) {
  const { project, busy, revision, selection } = props;
  const callbacks = useRef(props); callbacks.current = props;
  const [batches, setBatches] = useState<LSDBBatch[]>([]);
  const [batchId, setBatchId] = useState("");
  const [mode, setMode] = useState<"observed" | "fused">("fused");
  const [analysis, setAnalysis] = useState<LSDBAnalysis | null>(null);
  const [error, setError] = useState(""); const [running, setRunning] = useState(false);
  const [importing, setImporting] = useState(false); const [formOpen, setFormOpen] = useState(false);
  const [protocol, setProtocol] = useState<"ospf" | "isis">("ospf");
  const [vendor, setVendor] = useState<Vendor>("auto");
  const [processId, setProcessId] = useState("1");
  const [scope, setScope] = useState("0.0.0.0");
  const [sourceId, setSourceId] = useState(""); const [destinationId, setDestinationId] = useState("");
  const [selectedPath, setSelectedPath] = useState<number | null>(null);
  const [differencesOnly, setDifferencesOnly] = useState(true);
  const [mappingNode, setMappingNode] = useState(""); const [mappingInstance, setMappingInstance] = useState("");
  const form = useRef<HTMLFormElement>(null);
  const request = useRef<AbortController | null>(null); const generation = useRef(0);
  const selectedBatch = batches.find(b => b.id === batchId) || batches[0];
  const activeId = selectedBatch?.id || "";
  useEffect(() => { callbacks.current.onBatch?.(activeId); }, [activeId]);
  const names = useMemo(() => new Map(analysis?.nodes.map(n => [n.id, n.label])), [analysis?.nodes]);
  const routers = analysis?.nodes.filter(n => n.kind === "device" && n.observed !== false) || [];
  const source = routers.find(n => n.id === sourceId) || routers[0];
  const destination = analysis?.destinations.find(d => d.id === destinationId) || analysis?.destinations.find(d => d.node_id !== source?.id) || analysis?.destinations[0];
  const name = (id: string) => names.get(id) || id;
  const selectedNode = analysis?.nodes.find(n => n.id === selection?.id);
  const selectedEdge = analysis?.topology.edges.find(e => e.id === selection?.id);
  const cfgInstances = (project.snapshot?.routing?.instances || []).filter(i => i.protocol === selectedBatch?.protocol);
  const targetNode = routers.find(n => n.protocol_id === mappingNode) || routers[0];

  useEffect(() => {
    const controller = new AbortController();
    api<{ batches: LSDBBatch[] }>(`/api/projects/${project.id}/lsdb`, { signal: controller.signal })
      .then(r => setBatches(r.batches)).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort();
  }, [project.id, project.version]);

  useEffect(() => {
    const token = ++generation.current; request.current?.abort();
    setAnalysis(null); setError(""); setSelectedPath(null); callbacks.current.onGraph(null, null);
    if (!activeId) { setRunning(false); return; }
    const controller = new AbortController(); request.current = controller; setRunning(true);
    api<LSDBAnalysis>(`/api/projects/${project.id}/lsdb/${activeId}/analyze`, { method: "POST", signal: controller.signal,
      body: JSON.stringify({ version: project.version, mode }) }).then(r => {
      if (generation.current !== token) return;
      setAnalysis(r); callbacks.current.onGraph(r.topology, null);
    }).catch(e => { if (generation.current === token && !controller.signal.aborted) setError(String(e)); })
      .finally(() => { if (generation.current === token) setRunning(false); });
    return () => { controller.abort(); generation.current++; };
  }, [activeId, mode, project.id, project.version, revision]);

  const clearSimulation = () => {
    generation.current++; request.current?.abort(); setRunning(false); setError(""); setSelectedPath(null);
    if (analysis) { setAnalysis({ ...analysis, simulation: null, config_simulation: null }); callbacks.current.onGraph(analysis.topology, null); }
  };
  const highlight = (index: number | null, value = analysis) => {
    setSelectedPath(index);
    const sim = value?.simulation;
    const ids = index === null ? null : new Set(sim?.paths[index]?.hops);
    callbacks.current.onGraph(value?.topology || null, sim?.status === "ok" ? { ...sim, hops: ids ? sim.hops.filter(h => ids.has(h.id)) : sim.hops } : null);
  };
  const calculate = async () => {
    if (!source || !destination) return;
    clearSimulation(); const token = generation.current;
    const controller = new AbortController(); request.current = controller; setRunning(true);
    try {
      const value = await api<LSDBAnalysis>(`/api/projects/${project.id}/lsdb/${activeId}/analyze`, { method: "POST", signal: controller.signal,
        body: JSON.stringify({ version: project.version, mode, source: source.id, destination: destination.id }) });
      if (generation.current !== token) return;
      setAnalysis(value); highlight(null, value);
    } catch (e) { if (generation.current === token && !controller.signal.aborted) setError(String(e)); }
    finally { if (generation.current === token) setRunning(false); }
  };
  const imported = (value: { batch: LSDBBatch; project_version: number }) => {
    setBatches(prev => [value.batch, ...prev]); setBatchId(value.batch.id);
    callbacks.current.onVersion(value.project_version); setFormOpen(false);
  };
  const upload = async (event: React.FormEvent) => {
    event.preventDefault(); if (!form.current) return;
    generation.current++; request.current?.abort(); setAnalysis(null); callbacks.current.onGraph(null, null);
    setImporting(true); setError("");
    const body = new FormData(form.current); body.set("expected_version", String(project.version));
    body.set("vendor", vendor);
    const date = String(body.get("collected_at") || "");
    if (date) body.set("collected_at", new Date(date).toISOString());
    try { imported(await api(`/api/projects/${project.id}/lsdb`, { method: "POST", body })); }
    catch (e) { setError(String(e)); } finally { setImporting(false); }
  };
  const demo = async (p: "ospf" | "isis") => {
    generation.current++; request.current?.abort(); setAnalysis(null); callbacks.current.onGraph(null, null);
    setImporting(true); setError("");
    try { imported(await api(`/api/projects/${project.id}/lsdb/demo?protocol=${p}&vendor=${vendor === "auto" ? "huawei" : vendor}`, { method: "POST", body: JSON.stringify({ version: project.version }) })); }
    catch (e) { setError(String(e)); } finally { setImporting(false); }
  };
  const saveMapping = async (reset = false) => {
    if (!analysis || !targetNode) return;
    const mappings = { ...analysis.mappings };
    if (reset) delete mappings[targetNode.protocol_id];
    else if (mappingInstance) mappings[targetNode.protocol_id] = mappingInstance;
    else return;
    setImporting(true); setError("");
    try {
      const value = await api<{ project_version: number }>(`/api/projects/${project.id}/lsdb/${activeId}/mappings`, { method: "PUT", body: JSON.stringify({ version: project.version, mappings }) });
      callbacks.current.onVersion(value.project_version);
    } catch (e) { setError(String(e)); } finally { setImporting(false); }
  };
  const refs = (items: Ref[] | undefined, label: string) => items?.[0] ? <button className="text-button" onClick={() => callbacks.current.onSource(items[0])}>{label}</button> : null;
  const comparisons = analysis?.comparisons.filter(c => !differencesOnly || c.status !== "consistent") || [];
  const sim = analysis?.simulation;
  const ignoredSemantics = [...new Map([...(sim?.diagnostics || []), ...(analysis?.config_simulation?.diagnostics || [])]
    .filter(d => d.ignored).map(d => [`${d.code}:${d.message}`, d])).values()];
  return <aside className="inspector simulation-panel lsdb-panel" aria-label="LSDB 拓扑面板">
    <div className="simulation-heading"><Database size={20} /><div><h2>LSDB 拓扑</h2><small>Huawei · Cisco · Juniper · Nokia</small></div><button aria-label="关闭 LSDB" onClick={props.onClose}><X size={16} /></button></div>
    <div className="simulation-content">
      <button className="primary" onClick={() => setFormOpen(!formOpen)} disabled={importing || busy}><Upload size={15} />导入 LSDB</button>
      <label>采集厂商<select aria-label="LSDB 厂商" value={vendor} onChange={e => { const v = e.target.value as Vendor; setVendor(v); setProcessId(v === "juniper" ? "default" : v === "nokia" ? "0" : "1"); }}>{Object.entries(vendors).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      {formOpen && <form ref={form} onSubmit={e => void upload(e)} className="lsdb-import">
        <label>批次名称<input name="name" required maxLength={200} placeholder="核心网 · 采集日期" /></label>
        <label>协议<select name="protocol" value={protocol} onChange={e => { const p = e.target.value as "ospf" | "isis"; setProtocol(p); setScope(p === "ospf" ? "0.0.0.0" : "2"); }}><option value="ospf">OSPFv2</option><option value="isis">IS-IS</option></select></label>
        <div className="lsdb-fields"><label>本机进程 / 实例<input name="process_id" value={processId} onChange={e => setProcessId(e.target.value)} required /></label><label>{protocol === "ospf" ? "Area" : "Level"}<input name="scope" value={scope} onChange={e => setScope(e.target.value)} required /></label></div>
        <label>配置 VRF 名称<input name="context_name" defaultValue="default" required /></label>
        <details><summary>采集信息</summary><label>采集设备<input name="collector" placeholder="可留空，不按主机名匹配" /></label><label>采集时间（本地时区）<input name="collected_at" type="datetime-local" /></label><label>域标签<input name="domain" defaultValue="default" required /></label></details>
        <p className="simulation-note">{captureHints[vendor][protocol === "ospf" ? 0 : 1]} 上传同一设备、同一时刻的完整输出；每批次只分析所选进程与范围。</p>
        <input aria-label="LSDB 文本文件" type="file" name="files" accept=".txt,.log,.out,.text" multiple required />
        <label className="lsdb-check"><input type="checkbox" name="declared_complete" value="true" />已采集该范围完整 LSDB，且输出没有分页截断</label>
        <button type="submit" disabled={importing || busy}>{importing ? "正在导入…" : "保存采集批次"}</button>
      </form>}
      {!batches.length && !formOpen && <div className="simulation-empty"><p>独立保存 LSDB，按 Router ID / System ID 关联配置。没有配置也能还原逻辑拓扑。</p><button disabled={importing || busy} onClick={() => void demo("ospf")}>载入 OSPF LSDB 示例</button><button disabled={importing || busy} onClick={() => void demo("isis")}>载入 IS-IS LSDB 示例</button></div>}
      {!!batches.length && <>
        <label>采集批次<select aria-label="LSDB 采集批次" value={activeId} onChange={e => { setBatchId(e.target.value); setSourceId(""); setDestinationId(""); }}>
          {batches.map(b => <option key={b.id} value={b.id}>{b.name} · {b.protocol.toUpperCase()} {b.scope}</option>)}
        </select></label>
        <p className="simulation-note">{vendors[selectedBatch?.vendor || "huawei"]} · {selectedBatch?.collector || "采集设备未填写"} · {selectedBatch?.collected_at ? new Date(selectedBatch.collected_at).toLocaleString() : "采集时间未知"}<br />导入于 {selectedBatch && new Date(selectedBatch.imported_at).toLocaleString()} · {selectedBatch?.record_count} 条记录 · {selectedBatch?.context_name}</p>
        <div className="segmented"><button className={mode === "observed" ? "active" : ""} onClick={() => setMode("observed")}>LSDB 观测</button><button className={mode === "fused" ? "active" : ""} onClick={() => setMode("fused")}>结合配置</button></div>
        <p className="simulation-note">观测开销用于 LSDB 路径。仅配置的连接用虚线标示，不加入观测 SPF。<button className="text-button" onClick={props.onReveal}>查看拓扑</button></p>
      </>}
      {error && <p className="simulation-warning" role="alert">{error}</p>}
      {running && !analysis && <p role="status">正在还原拓扑…</p>}
      {analysis && <>
        <div className="simulation-stats"><div><strong>{routers.length}</strong><span>路由设备</span></div><div><strong>{routers.filter(n => n.instance_id).length}</strong><span>已关联配置</span></div></div>
        {(selectedNode || selectedEdge) && <div className="lsdb-evidence"><b>{selectedNode ? `${selectedNode.label} · ${selectedNode.protocol_id}` : `${name(selectedEdge!.source)} → ${name(selectedEdge!.target)}`}</b>{refs((selectedNode?.source_refs || (selectedEdge as unknown as { source_refs: Ref[] })?.source_refs), "LSDB 来源")}</div>}
        <label>源设备<select aria-label="LSDB 源设备" value={source?.id || ""} onChange={e => { clearSimulation(); setSourceId(e.target.value); }}>{routers.map(n => <option key={n.id} value={n.id}>{n.label} · {n.protocol_id}</option>)}</select></label>
        <label>目的设备 / 发布前缀<select aria-label="LSDB 目的前缀" value={destination?.id || ""} onChange={e => { clearSimulation(); setDestinationId(e.target.value); }}>{analysis.destinations.map(d => <option key={d.id} value={d.id}>{name(d.node_id)} · {d.prefix}</option>)}</select></label>
        <button className="primary simulation-run" disabled={running || importing || busy || !destination || !source} onClick={() => void calculate()}><Play size={15} />{running ? "正在计算…" : "计算 LSDB 最短路径"}</button>
        {sim && <div className="simulation-result" aria-live="polite">
          {sim.status === "ok" ? <>
            <div className="simulation-stats"><div><strong>{sim.total_cost}</strong><span>观测总开销</span></div><div><strong>{sim.path_count}</strong><span>等价路径</span></div></div>
            {analysis.config_simulation && <div className="lsdb-evidence">配置推演：{analysis.config_simulation.status === "ok" ? `开销 ${analysis.config_simulation.total_cost} · ${analysis.config_simulation.path_count} 条路径` : "配置数据不足或所选范围不可达"}。<small> 两种结果独立计算，可在下方查看连接与开销差异。</small></div>}
            <p className="simulation-note">协议候选路径 · 理论逐跳均匀分担{sim.forwarding_status === "limit_ambiguous" ? " · 默认策略或 ECMP 上限影响实际子集" : " · 非实测流量"}</p>
            <button onClick={() => highlight(null)} className={selectedPath === null ? "active" : ""}>全部 LSDB 路径</button>
            <div className="simulation-paths">{sim.paths.map((p,i) => <button key={i} className={`simulation-path ${selectedPath === i ? "active" : ""}`} onClick={() => highlight(i)}><span><b>路径 {i+1}</b><strong>{percent(p.share_percent)}</strong></span><span>{p.device_ids.map((id,j) => <span key={`${id}:${j}`}>{j > 0 && <ArrowRight size={12} />}{name(id)}</span>)}</span></button>)}</div>
            {sim.paths_truncated && <p className="simulation-note">路径列表已截断；计数与占比仍按完整路径集合计算。</p>}
            <details><summary>逐跳开销与分担</summary>{sim.hops.filter(h => selectedPath === null || sim.paths[selectedPath].hops.includes(h.id)).map(h => <div className="simulation-hop" key={h.id}><b>{name(h.source_device_id)} → {name(h.target_device_id)}</b><div>{h.interface_name || "出口端口未关联"} · {h.next_hop || "下一跳 IP 未知"}</div><div>开销 {h.cost} · 流量 {percent(h.share_percent)} · 本机 {percent(h.local_share_percent)}{refs(h.source_refs,"LSDB 来源")}</div></div>)}</details>
          </> : <p className="simulation-warning">{{ incomplete: "数据不足，未生成路径", unsupported: "当前模型不能确定路径", unreachable: "所选范围内不可达" }[sim.status]}</p>}
          <details><summary>路径诊断与假设</summary>{sim.diagnostics.filter(d => !d.ignored).map((d,i) => <p key={i}>{d.message}</p>)}{sim.assumptions.map((s,i) => <p key={`a${i}`}>{s}</p>)}</details>
          {ignoredSemantics.length > 0 && <details><summary>已忽略的配置语义 · {ignoredSemantics.length}</summary>{ignoredSemantics.map((d,i) => <p key={i}>{d.message}{refs(d.source_refs, "查看来源")}</p>)}</details>}
        </div>}
        {mode === "fused" && <>
          <details className="lsdb-comparisons" open><summary>配置与观测对比 · {analysis.comparisons.length}</summary><label className="lsdb-check"><input type="checkbox" checked={differencesOnly} onChange={e => setDifferencesOnly(e.target.checked)} />仅看差异</label>
            {!comparisons.length && <p className="simulation-note">{analysis.comparisons.length ? "已关联连接的开销一致。" : "尚无可对比的配置连接。"}</p>}
            {comparisons.slice(0,200).map(c => <div className="lsdb-evidence" key={c.id}><div><b>{name(c.source)} → {name(c.target)}</b><span className={`lsdb-status ${c.status}`}>{lsdbStatus(c.status)}</span></div><div>{c.interface_name || "端口未关联"} · 配置 {c.config_cost ?? "未知"} / 观测 {c.observed_cost ?? "未见"}</div>{refs(c.source_refs,"LSDB 来源")}{refs(c.config_source_refs,"配置来源")}</div>)}
            {comparisons.length > 200 && <p className="simulation-note">显示前 200 条；完整记录包含在分析 API 结果中。</p>}
          </details>
          {!!cfgInstances.length && <details><summary>手动关联设备身份</summary><label>LSDB 节点<select aria-label="关联 LSDB 节点" value={targetNode?.protocol_id || ""} onChange={e => setMappingNode(e.target.value)}>{routers.map(n => <option key={n.id} value={n.protocol_id}>{n.label} · {n.protocol_id} · {lsdbStatus(n.mapping_status)}</option>)}</select></label><label>配置实例<select aria-label="关联配置实例" value={mappingInstance} onChange={e => setMappingInstance(e.target.value)}><option value="">请选择配置实例</option>{cfgInstances.map(i => <option key={i.id} value={i.id}>{project.snapshot?.devices.find(d => d.id === i.device_id)?.hostname} · 进程 {i.local_name} · {project.snapshot?.contexts.find(c => c.id === i.routing_context_id)?.local_name}</option>)}</select></label><button disabled={importing || !mappingInstance} onClick={() => void saveMapping()}>保存身份关联</button><button disabled={importing || !targetNode || !analysis.mappings[targetNode.protocol_id]} onClick={() => void saveMapping(true)}>恢复自动关联</button></details>}
        </>}
        {!!analysis.diagnostics.length && <details className="simulation-diagnostics" open={analysis.diagnostics.some(d => d.blocking)}><summary>采集与拓扑诊断 · {analysis.diagnostics.length}</summary>{analysis.diagnostics.slice(0,200).map((d,i) => <p key={i}>{d.blocking ? "需补齐：" : "提示："}{d.message}{refs(d.source_refs,"查看来源")}</p>)}</details>}
      </>}
      {!!batches.length && <details><summary>合成示例</summary><p className="simulation-note">按上方厂商选择载入原生格式（自动识别时使用华为）。与四厂商混合配置示例对应，可验证 50% / 25% / 25% 分担。</p><button disabled={importing || busy} onClick={() => void demo("ospf")}>载入 OSPF LSDB 示例</button><button disabled={importing || busy} onClick={() => void demo("isis")}>载入 IS-IS LSDB 示例</button></details>}
    </div>
  </aside>;
}
