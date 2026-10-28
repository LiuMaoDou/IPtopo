import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Play, Route, X } from "lucide-react";
import { api, type Project, type Ref } from "./types";
import type { SimulationHighlight, SimulationResult } from "./routingTypes";
import { simulationCandidates, simulationContextKey, type SimulationEndpoints } from "./simulationModel";

type Props = {
  project: Project;
  endpoints: SimulationEndpoints;
  onEndpoints: (value: SimulationEndpoints) => void;
  onHighlight: (value: SimulationHighlight | null) => void;
  onSource: (ref: Ref) => void;
  onClose: () => void;
  onReveal: () => void;
  onDemo: () => void;
  busy: boolean;
};
const percent = (n: number) => `${Number(n.toFixed(2))}%`;

export default function SimulationPanel({ project, endpoints, onEndpoints, onHighlight, onSource, onClose, onReveal, onDemo, busy }: Props) {
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
  const devices = useMemo(() => new Map(project.snapshot?.devices.map(d => [d.id, d])), [project.snapshot?.devices]);
  const interfaces = useMemo(() => new Map(project.snapshot?.interfaces.map(i => [i.id, i])), [project.snapshot?.interfaces]);
  const { protocol } = endpoints;
  const { instances, sourceInstances, source, scopes, scope, bindings, bindingIds, destinationAdvertisements: destinations, destination } = useMemo(
    () => simulationCandidates(project, endpoints), [project.snapshot, endpoints]);
  const contextKey = simulationContextKey(project);
  const latest = useRef({ contextKey, endpoints, busy });
  latest.current = { contextKey, endpoints, busy };
  const adjustable = bindings.find(b => b.id === scenarioBinding) || bindings[0];
  const name = (id: string) => devices.get(id)?.hostname || id;
  const ifaceName = (id: string) => interfaces.get(id)?.raw_name || id;
  const bindingName = (id: string) => {
    const b = bindings.find(item => item.id === id);
    return b ? `${name(b.device_id)} · ${ifaceName(b.interface_id)}` : id;
  };
  const instanceName = (id: string) => {
    const instance = instances.find(item => item.id === id);
    const context = project.snapshot?.contexts.find(item => item.id === instance?.routing_context_id);
    return instance ? `进程 ${instance.local_name} · ${context?.local_name || "默认实例"}${instance.router_id ? ` · ${instance.router_id}` : ""}` : id;
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
  useEffect(() => { invalidate(); }, [endpoints, busy]);
  const changeEndpoints = (value: Partial<SimulationEndpoints>) => {
    invalidate(); onEndpoints({ ...endpoints, ...value });
  };
  const changeProtocol = (value: "ospf" | "isis") => {
    changeEndpoints({ protocol: value, sourceInstanceId: "", destinationAdvertisementId: "", scope: "" });
    setMetricOverrides({}); setDisabledInterfaces([]); setScenarioBinding(""); setScenarioCost("");
  };
  const selectPath = (index: number | null, value = result) => {
    setSelectedPath(index);
    if (!value || value.status !== "ok") { onHighlight(null); return; }
    const ids = index === null ? null : new Set(value.paths[index].hops);
    onHighlight({ ...value, hops: ids ? value.hops.filter(h => ids.has(h.id)) : value.hops });
  };
  const calculate = async () => {
    if (busy || !source || !destination || !scope) return;
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
      if (token !== generation.current || controller.signal.aborted || latest.current.contextKey !== contextKey
        || latest.current.endpoints !== endpoints || latest.current.busy) return;
      setResult(value); selectPath(null, value);
    } catch (e) {
      if (token === generation.current && !controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (token === generation.current) setRunning(false);
    }
  };
  const invalidCost = unknownCost !== "" && (!Number.isInteger(Number(unknownCost)) || Number(unknownCost) < 1 || Number(unknownCost) > 65535);
  const diagnostics = result?.diagnostics.filter(item => !item.ignored) || [];
  const ignored = result?.diagnostics.filter(item => item.ignored) || [];

  return <aside className="inspector simulation-panel" aria-label="路径仿真面板">
    <div className="simulation-heading"><Route size={20} /><div><h2>路径仿真</h2><small>四厂商 · IPv4 · 配置推演</small></div><button aria-label="关闭路径仿真" onClick={onClose}><X size={16} /></button></div>
    <div className="simulation-content">
      <div className="segmented simulation-protocol" aria-label="仿真协议">
        <button disabled={busy} className={protocol === "ospf" ? "active" : ""} onClick={() => changeProtocol("ospf")}>OSPF</button>
        <button disabled={busy} className={protocol === "isis" ? "active" : ""} onClick={() => changeProtocol("isis")}>IS-IS</button>
      </div>
      {busy && <p role="status" className="simulation-note">项目正在更新，旧选择、场景和计算结果已清除；完成后请重新选择端点。</p>}
      <fieldset className="simulation-endpoints" disabled={busy}>
        <p className="simulation-note">可在配置画布上右键设备，设为仿真源或目的。多实例 / Loopback 需明确选择。</p>
        <label>源设备<select aria-label="仿真源设备" value={endpoints.sourceDeviceId} onChange={e => changeEndpoints({ sourceDeviceId: e.target.value, sourceInstanceId: "" })}>
          <option value="">请选择源设备</option>
          {[...devices.values()].map(device => <option key={device.id} value={device.id}>{device.hostname}</option>)}
        </select></label>
        <label>源协议实例<select aria-label="仿真源协议实例" value={source?.id || ""} onChange={e => changeEndpoints({ sourceInstanceId: e.target.value })} disabled={!sourceInstances.length}>
          {!source && <option value="">{sourceInstances.length > 1 ? `请选择协议实例（${sourceInstances.length} 个候选）` : "没有可用实例"}</option>}
          {sourceInstances.map(instance => <option key={instance.id} value={instance.id}>{instanceName(instance.id)}</option>)}
        </select></label>
        {endpoints.sourceDeviceId && !sourceInstances.length && <p className="simulation-warning">{name(endpoints.sourceDeviceId)} 没有可用的 {protocol === "ospf" ? "OSPF" : "IS-IS"} 接口绑定，请切换协议或检查导入配置。</p>}
        <label>{protocol === "ospf" ? "OSPF Area" : "IS-IS Level"}<select aria-label="仿真协议范围" value={scope} onChange={e => changeEndpoints({ scope: e.target.value, destinationAdvertisementId: "" })} disabled={!scopes.length}>
          {!scope && <option value="">{scopes.length > 1 ? "请选择协议范围" : "请先选择源实例"}</option>}
          {scopes.map(s => <option key={s} value={s}>{protocol === "ospf" ? `Area ${s}` : `Level ${s}`}</option>)}
        </select></label>
        <label>目的设备<select aria-label="仿真目的设备" value={endpoints.destinationDeviceId} onChange={e => changeEndpoints({ destinationDeviceId: e.target.value, destinationAdvertisementId: "" })}>
          <option value="">请选择目的设备</option>
          {[...devices.values()].map(device => <option key={device.id} value={device.id}>{device.hostname}</option>)}
        </select></label>
        <label>目的 Loopback<select aria-label="仿真目的Loopback" value={destination?.id || ""} onChange={e => changeEndpoints({ destinationAdvertisementId: e.target.value })} disabled={!destinations.length}>
          {!destination && <option value="">{destinations.length > 1 ? `请选择 Loopback（${destinations.length} 个候选）` : "当前范围没有已发布的 /32 Loopback"}</option>}
          {destinations.map(advertisement => {
            const binding = bindings.find(item => item.id === advertisement.binding_id);
            return <option key={advertisement.id} value={advertisement.id}>{advertisement.address} · {binding ? instanceName(binding.instance_id) : ""}</option>;
          })}
        </select></label>
        {endpoints.destinationDeviceId && scope && !destinations.length && <p className="simulation-warning">{name(endpoints.destinationDeviceId)} 在当前协议 / 范围内没有已发布的 IPv4 /32 Loopback，请检查实例、Area / Level 和配置来源。</p>}
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
        <button className="primary simulation-run" disabled={running || busy || !source || !destination || !scope || invalidCost} onClick={() => void calculate()}><Play size={15} />{running ? "正在计算…" : "计算最短路径"}</button>
        <p className="simulation-note">单 Area / Level 内部路由。画布筛选不影响计算。<button className="text-button" onClick={onReveal}>显示完整拓扑</button></p>
      </fieldset>
      {!instances.length && <div className="simulation-empty"><p>此项目没有可用的 {protocol === "ospf" ? "OSPF" : "IS-IS"} 协议事实。</p><p>请导入 Huawei、Cisco、Juniper 或 Nokia 的完整配置，或打开独立的混合组网示例。</p><button disabled={busy} onClick={onDemo}>载入四厂商仿真示例</button></div>}
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
        {diagnostics.length > 0 && <details className="simulation-diagnostics" open={result.status !== "ok" || result.forwarding_status === "limit_ambiguous"}><summary>诊断 · {diagnostics.length}</summary>{diagnostics.map((d, i) => <p key={i}>{d.message}{d.interface_id && <small> {ifaceName(d.interface_id)}</small>}{d.source_refs?.[0] && <button className="text-button" onClick={() => onSource(d.source_refs![0])}>查看来源</button>}</p>)}</details>}
        {ignored.length > 0 && <details className="simulation-assumptions"><summary>已忽略的配置语义 · {ignored.length}</summary>{ignored.map((d, i) => <p key={i}>{d.message}{d.source_refs?.[0] && <button className="text-button" onClick={() => onSource(d.source_refs![0])}>查看来源</button>}</p>)}</details>}
        <details className="simulation-assumptions"><summary>计算假设</summary>{result.assumptions.map((a, i) => <p key={i}>{a}</p>)}</details>
      </div>}
    </div>
  </aside>;
}
