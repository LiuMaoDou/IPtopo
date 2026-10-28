import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Play, Route, X } from "lucide-react";
import { api, type Project, type Ref } from "./types";
import type { SimulationHighlight, SimulationResult } from "./routingTypes";

type Props = {
  project: Project;
  onHighlight: (value: SimulationHighlight | null) => void;
  onSource: (ref: Ref) => void;
  onClose: () => void;
  onReveal: () => void;
  onDemo: () => void;
  busy: boolean;
};
const percent = (n: number) => `${Number(n.toFixed(2))}%`;

export default function SimulationPanel({ project, onHighlight, onSource, onClose, onReveal, onDemo, busy }: Props) {
  const [protocol, setProtocol] = useState<"ospf" | "isis">("ospf");
  const [sourceChoice, setSourceChoice] = useState("");
  const [destinationChoice, setDestinationChoice] = useState("");
  const [scopeChoice, setScopeChoice] = useState("");
  const [unknownCost, setUnknownCost] = useState("");
  const [disabledInterfaces, setDisabledInterfaces] = useState<string[]>([]);
  const [metricOverrides, setMetricOverrides] = useState<Record<string, number>>({});
  const [scenarioBinding, setScenarioBinding] = useState("");
  const [scenarioCost, setScenarioCost] = useState("");
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [selectedPath, setSelectedPath] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const facts = project.snapshot?.routing;
  const devices = useMemo(() => new Map(project.snapshot?.devices.map(d => [d.id, d])), [project.snapshot?.devices]);
  const interfaces = useMemo(() => new Map(project.snapshot?.interfaces.map(i => [i.id, i])), [project.snapshot?.interfaces]);
  const instances = useMemo(() => {
    const bound = new Set((facts?.bindings || []).map(b => b.instance_id));
    return (facts?.instances || []).filter(i => i.protocol === protocol && bound.has(i.id));
  }, [facts, protocol]);
  const instanceIds = useMemo(() => new Set(instances.map(i => i.id)), [instances]);
  const source = instances.find(i => i.id === sourceChoice) || instances[0];
  const scopes = [...new Set((facts?.bindings || []).filter(b => b.instance_id === source?.id).map(b => b.scope))].sort();
  const scope = scopes.includes(scopeChoice) ? scopeChoice : scopes[0] || "";
  const bindings = useMemo(() => (facts?.bindings || []).filter(b => instanceIds.has(b.instance_id) && b.scope === scope), [facts, instanceIds, scope]);
  const bindingIds = useMemo(() => new Set(bindings.map(b => b.id)), [bindings]);
  const destinations = useMemo(() => (facts?.advertisements || []).filter(a => bindingIds.has(a.binding_id)
    && a.prefix.endsWith("/32") && interfaces.get(a.interface_id)?.kind === "loopback"), [facts, bindingIds, interfaces]);
  const destination = destinations.find(a => a.id === destinationChoice)
    || destinations.find(a => a.device_id !== source?.device_id) || destinations[0];
  const adjustable = bindings.find(b => b.id === scenarioBinding) || bindings[0];
  const name = (id: string) => devices.get(id)?.hostname || id;
  const ifaceName = (id: string) => interfaces.get(id)?.raw_name || id;
  const bindingName = (id: string) => {
    const b = bindings.find(item => item.id === id);
    return b ? `${name(b.device_id)} · ${ifaceName(b.interface_id)}` : id;
  };

  useEffect(() => () => { generation.current++; request.current?.abort(); }, []);

  const invalidate = () => {
    generation.current++;
    request.current?.abort();
    setRunning(false);
    setResult(null);
    setSelectedPath(null);
    setError("");
    onHighlight(null);
  };
  const changeProtocol = (value: "ospf" | "isis") => {
    invalidate(); setProtocol(value); setSourceChoice(""); setDestinationChoice("");
    setScopeChoice(""); setMetricOverrides({}); setDisabledInterfaces([]); setScenarioBinding("");
  };
  const selectPath = (index: number | null, value = result) => {
    setSelectedPath(index);
    if (!value || value.status !== "ok") { onHighlight(null); return; }
    const ids = index === null ? null : new Set(value.paths[index].hops);
    onHighlight({ ...value, hops: ids ? value.hops.filter(h => ids.has(h.id)) : value.hops });
  };
  const calculate = async () => {
    if (!source || !destination || !scope) return;
    invalidate();
    const token = generation.current;
    const controller = new AbortController();
    request.current = controller;
    setRunning(true);
    try {
      const value = await api<SimulationResult>(`/api/projects/${project.id}/simulate`, {
        method: "POST", signal: controller.signal,
        body: JSON.stringify({ version: project.version, protocol, scope, source_instance_id: source.id,
          destination_advertisement_id: destination.id,
          scenario: { unknown_cost: unknownCost ? Number(unknownCost) : null,
            disabled_interfaces: disabledInterfaces,
            metric_overrides: Object.fromEntries(Object.entries(metricOverrides).filter(([id]) => bindingIds.has(id))) } }),
      });
      if (token !== generation.current) return;
      setResult(value); selectPath(null, value);
    } catch (e) {
      if (token === generation.current && !controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (token === generation.current) setRunning(false);
    }
  };
  const invalidCost = unknownCost !== "" && (!Number.isInteger(Number(unknownCost)) || Number(unknownCost) < 1 || Number(unknownCost) > 65535);

  return <aside className="inspector simulation-panel" aria-label="路径仿真面板">
    <div className="simulation-heading"><Route size={20} /><div><h2>路径仿真</h2><small>四厂商 · IPv4 · 配置推演</small></div><button aria-label="关闭路径仿真" onClick={onClose}><X size={16} /></button></div>
    <div className="simulation-content">
      <div className="segmented simulation-protocol" aria-label="仿真协议">
        <button className={protocol === "ospf" ? "active" : ""} onClick={() => changeProtocol("ospf")}>OSPF</button>
        <button className={protocol === "isis" ? "active" : ""} onClick={() => changeProtocol("isis")}>IS-IS</button>
      </div>
      {!instances.length ? <div className="simulation-empty"><p>此项目没有可用的 {protocol === "ospf" ? "OSPF" : "IS-IS"} 协议事实。</p><p>请导入 Huawei、Cisco、Juniper 或 Nokia 的完整配置，或打开独立的混合组网示例。</p><button disabled={busy} onClick={onDemo}>载入四厂商仿真示例</button></div> : <>
        <label>源设备 / 实例<select aria-label="仿真源设备" value={source?.id || ""} onChange={e => { invalidate(); setSourceChoice(e.target.value); }}>
          {instances.map(i => <option key={i.id} value={i.id}>{name(i.device_id)} · 进程 {i.local_name} · {project.snapshot?.contexts.find(c => c.id === i.routing_context_id)?.local_name}</option>)}
        </select></label>
        <label>{protocol === "ospf" ? "OSPF Area" : "IS-IS Level"}<select aria-label="仿真协议范围" value={scope} onChange={e => { invalidate(); setScopeChoice(e.target.value); }}>
          {scopes.map(s => <option key={s} value={s}>{protocol === "ospf" ? `Area ${s}` : `Level ${s}`}</option>)}
        </select></label>
        <label>目的设备 / Loopback<select aria-label="仿真目的设备" value={destination?.id || ""} onChange={e => { invalidate(); setDestinationChoice(e.target.value); }}>
          {!destinations.length && <option value="">没有已发布的 /32 Loopback</option>}
          {destinations.map(a => <option key={a.id} value={a.id}>{name(a.device_id)} · {a.address}</option>)}
        </select></label>
        <details className="simulation-scenario"><summary>场景调整 <small>独立于原配置</small></summary>
          <label>未知开销假设<input aria-label="未知开销假设" type="number" min="1" max="65535" placeholder="留空时遇到未知开销会停止计算" value={unknownCost} onChange={e => { invalidate(); setUnknownCost(e.target.value); }} /></label>
          <label>调整接口<select aria-label="场景接口" value={adjustable?.id || ""} onChange={e => setScenarioBinding(e.target.value)}>
            {bindings.map(b => <option key={b.id} value={b.id}>{bindingName(b.id)} · {b.cost === null ? "开销未知" : `开销 ${b.cost}`}</option>)}
          </select></label>
          <div className="simulation-scenario-actions"><input aria-label="接口场景开销" type="number" min="1" max={adjustable?.metric_limit || 65535} placeholder="新开销" value={scenarioCost} onChange={e => setScenarioCost(e.target.value)} />
            <button disabled={!adjustable || !Number.isInteger(Number(scenarioCost)) || Number(scenarioCost) < 1 || Number(scenarioCost) > adjustable.metric_limit} onClick={() => { if (adjustable) { invalidate(); setMetricOverrides(prev => ({ ...prev, [adjustable.id]: Number(scenarioCost) })); } }}>应用开销</button>
          </div>
          <button disabled={!adjustable || disabledInterfaces.includes(adjustable.interface_id)} onClick={() => { if (adjustable) { invalidate(); setDisabledInterfaces(prev => [...prev, adjustable.interface_id]); } }}>模拟此接口退出</button>
          {Object.entries(metricOverrides).filter(([id]) => bindingIds.has(id)).map(([id, value]) => <div className="simulation-adjustment" key={id}><span>{bindingName(id)} → {value}</span><button aria-label={`移除 ${bindingName(id)} 的开销覆盖`} onClick={() => { invalidate(); setMetricOverrides(prev => { const next = { ...prev }; delete next[id]; return next; }); }}><X size={13} /></button></div>)}
          {disabledInterfaces.map(id => <div className="simulation-adjustment" key={id}><span>退出：{name(interfaces.get(id)?.device_id || "")} · {ifaceName(id)}</span><button aria-label={`恢复 ${ifaceName(id)}`} onClick={() => { invalidate(); setDisabledInterfaces(prev => prev.filter(i => i !== id)); }}><X size={13} /></button></div>)}
        </details>
        <button className="primary simulation-run" disabled={running || busy || !destination || !scope || invalidCost} onClick={() => void calculate()}><Play size={15} />{running ? "正在计算…" : "计算最短路径"}</button>
        <p className="simulation-note">单 Area / Level 内部路由。画布筛选不影响计算。<button className="text-button" onClick={onReveal}>显示完整拓扑</button></p>
      </>}
      {error && <p role="alert" className="simulation-warning">{error}</p>}
      {result && <div className="simulation-result" aria-live="polite">
        {result.status === "ok" ? <>
          <div className="simulation-stats"><div><strong>{result.total_cost}</strong><span>总开销</span></div><div><strong>{result.path_count}</strong><span>等价路径</span></div></div>
          <p className="simulation-note">协议候选路径 · 理论逐跳均匀分担{result.forwarding_status === "limit_ambiguous" ? " · 默认策略或 ECMP 上限影响实际选路" : " · 实际 FIB / Hash 可能不同"}</p>
          <button className={selectedPath === null ? "active" : ""} onClick={() => selectPath(null)}>全部路径</button>
          <div className="simulation-paths">{result.paths.map((path, index) => <button key={index} className={`simulation-path ${selectedPath === index ? "active" : ""}`} onClick={() => selectPath(index)}>
            <span><b>路径 {index + 1}</b><strong>{percent(path.share_percent)}</strong></span><span>{path.device_ids.map((id, i) => <span key={`${id}:${i}`}>{i > 0 && <ArrowRight size={12} />}{name(id)}</span>)}</span>
          </button>)}</div>
          {result.paths_truncated && <p className="simulation-note">仅展开前 {result.paths.length} 条，分担占比按完整路径集合计算。</p>}
          <h3>逐跳出口</h3>
          {result.hops.filter(h => selectedPath === null || result.paths[selectedPath].hops.includes(h.id)).map(h => <div className="simulation-hop" key={h.id}>
            <div><b>{name(h.source_device_id)} → {name(h.target_device_id)}</b><strong>{percent(h.share_percent)}</strong></div>
            <div>{ifaceName(h.interface_id)} → {h.next_hop}</div><div>开销 {h.cost} · 本机分担 {percent(h.local_share_percent)}{h.source_refs[0] && <button className="text-button" onClick={() => onSource(h.source_refs[0])}>配置来源</button>}</div>
          </div>)}
        </> : <div className="simulation-warning"><b>{{ incomplete: "数据不足，未生成路径", unreachable: "所选范围内不可达", unsupported: "当前模型暂不支持" }[result.status]}</b></div>}
        {result.diagnostics.length > 0 && <details className="simulation-diagnostics" open={result.status !== "ok" || result.forwarding_status === "limit_ambiguous"}><summary>诊断 · {result.diagnostics.length}</summary>{result.diagnostics.map((d, i) => <p key={i}>{d.message}{d.interface_id && <small> {ifaceName(d.interface_id)}</small>}{d.source_refs?.[0] && <button className="text-button" onClick={() => onSource(d.source_refs![0])}>查看来源</button>}</p>)}</details>}
        <details className="simulation-assumptions"><summary>计算假设</summary>{result.assumptions.map((a, i) => <p key={i}>{a}</p>)}</details>
      </div>}
    </div>
  </aside>;
}
