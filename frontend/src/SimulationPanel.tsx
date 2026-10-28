import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Play, Route, X } from "lucide-react";
import { api, type Interface, type Project, type Ref } from "./types";
import type { PortSimulationResult, SimulationHighlight, SimulationResult } from "./routingTypes";
import { assignSimulationDevice, assignSimulationProtocol, portAddresses, simulationCandidates, simulationContextKey, usablePort, type SimulationEndpoints } from "./simulationModel";

type Props = {
  project: Project;
  endpoints: SimulationEndpoints;
  onEndpoints: (value: SimulationEndpoints) => void;
  onHighlight: (value: SimulationHighlight | null) => void;
  onSource: (ref: Ref) => void;
  onClose: () => void;
  onInterface: (id: string) => void;
  busy: boolean;
};
const percent = (n: number) => `${Number(n.toFixed(2))}%`;
const protocolName = (protocol: string) => protocol === "ospf" ? "OSPF" : "IS-IS";

export default function SimulationPanel({ project, endpoints, onEndpoints, onHighlight, onSource, onClose, onInterface, busy }: Props) {
  const [results, setResults] = useState<SimulationResult[]>([]);
  const [selected, setSelected] = useState<{ group: number; path: number | null }>({ group: 0, path: null });
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const devices = useMemo(() => new Map(project.snapshot?.devices.map(d => [d.id, d])), [project.snapshot?.devices]);
  const interfaces = useMemo(() => new Map(project.snapshot?.interfaces.map(i => [i.id, i])), [project.snapshot?.interfaces]);
  const { instances, protocols, protocol, sourceInterfaces, destinationInterfaces, source, destination } = useMemo(
    () => simulationCandidates(project, endpoints), [project.snapshot, endpoints]);
  const contextKey = simulationContextKey(project);
  const latest = useRef({ contextKey, endpoints, busy });
  latest.current = { contextKey, endpoints, busy };
  const name = (id: string) => devices.get(id)?.hostname || id;
  const ifaceName = (id: string) => interfaces.get(id)?.raw_name || id;
  const portLabel = (iface: Interface) => `${iface.raw_name} · ${portAddresses(iface).map(a => a.address).join(", ") || "地址不可用"}${usablePort(iface) ? "" : " · 未启用"}`;

  useEffect(() => () => { generation.current++; request.current?.abort(); }, []);
  const invalidate = () => {
    generation.current++;
    request.current?.abort();
    setRunning(false); setResults([]); setSelected({ group: 0, path: null }); setError("");
    onHighlight(null);
  };
  useEffect(() => { invalidate(); }, [endpoints, busy]);
  const changeEndpoints = (value: Partial<SimulationEndpoints>) => {
    invalidate(); onEndpoints({ ...endpoints, ...value });
  };
  const selectPath = (group: number, path: number | null, value = results) => {
    setSelected({ group, path });
    const result = value[group];
    if (!result || result.status !== "ok") { onHighlight(null); return; }
    const ids = path === null ? null : new Set(result.paths[path].hops);
    onHighlight({ ...result, hops: ids ? result.hops.filter(h => ids.has(h.id)) : result.hops });
  };
  const ready = !!(source && destination && usablePort(source) && usablePort(destination) && protocol);
  const calculate = async () => {
    if (busy || !ready) return;
    invalidate();
    const token = generation.current;
    const controller = new AbortController();
    request.current = controller; setRunning(true);
    try {
      const value = await api<PortSimulationResult>(`/api/projects/${project.id}/simulate`, {
        method: "POST", signal: controller.signal,
        body: JSON.stringify({ version: project.version, protocol,
          source_device_id: endpoints.sourceDeviceId, source_interface_id: endpoints.sourceInterfaceId,
          destination_device_id: endpoints.destinationDeviceId, destination_interface_id: endpoints.destinationInterfaceId }),
      });
      if (token !== generation.current || controller.signal.aborted || latest.current.contextKey !== contextKey
        || latest.current.endpoints !== endpoints || latest.current.busy) return;
      setResults(value.results);
      const first = value.results.findIndex(result => result.status === "ok");
      selectPath(Math.max(first, 0), null, value.results);
    } catch (e) {
      if (token === generation.current && !controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (token === generation.current) setRunning(false);
    }
  };

  return <aside className="inspector simulation-panel" aria-label="路径仿真面板">
    <div className="simulation-heading"><Route size={20} /><div><h2>路径仿真</h2><small>选择两端设备与端口</small></div><button aria-label="关闭路径仿真" onClick={onClose}><X size={16} /></button></div>
    <div className="simulation-content">
      {busy && <p role="status" className="simulation-note">项目正在更新，完成后请重新选择端点。</p>}
      <fieldset className="simulation-endpoints" disabled={busy}>
        {(["source", "destination"] as const).map(role => {
          const isSource = role === "source";
          const label = isSource ? "源" : "目的";
          const candidates = isSource ? sourceInterfaces : destinationInterfaces;
          const deviceId = isSource ? endpoints.sourceDeviceId : endpoints.destinationDeviceId;
          const interfaceId = (isSource ? source : destination)?.id || "";
          return <div key={role} className={`simulation-endpoint-card ${role}`}>
            <h3><span className="endpoint-dot" />{label}</h3>
            <label>{label}设备<select aria-label={`仿真${label}设备`} value={deviceId} onChange={e => {
              invalidate(); onEndpoints(assignSimulationDevice(endpoints, role, e.target.value));
            }}>
              <option value="">请选择{label}设备</option>
              {[...devices.values()].map(device => <option key={device.id} value={device.id}>{device.hostname}</option>)}
            </select></label>
            <label>{label}端口<select aria-label={`仿真${label}端口`} value={interfaceId} disabled={!deviceId || !candidates.length}
              onChange={e => changeEndpoints(isSource ? { sourceInterfaceId: e.target.value } : { destinationInterfaceId: e.target.value })}>
              <option value="">{deviceId && !candidates.length ? `没有使能${protocol ? ` ${protocolName(protocol)} ` : "路由协议"}的 IPv4 端口` : `请选择${label}端口`}</option>
              {candidates.map(iface => <option key={iface.id} value={iface.id} disabled={!usablePort(iface)}>{portLabel(iface)}</option>)}
            </select></label>
          </div>;
        })}
        {protocols.length > 1 ? <label>协议<select aria-label="仿真协议" value={protocol} onChange={e => {
          invalidate(); onEndpoints(assignSimulationProtocol(project, endpoints, e.target.value as SimulationEndpoints["protocol"]));
        }}>
          <option value="">请选择协议</option>
          {protocols.map(value => <option key={value} value={value}>{protocolName(value)}</option>)}
        </select></label> : protocol ? <p className="simulation-note simulation-detected-protocol">配置协议：{protocolName(protocol)}</p> : null}
        <button className="primary simulation-run" disabled={running || busy || !ready} onClick={() => void calculate()}><Play size={15} />{running ? "正在仿真…" : "仿真"}</button>
        <p className="simulation-note">仅显示已使能协议的 IPv4 端口。源端口提供源地址，出口由路由计算。选定两端后点击仿真显示路径。</p>
      </fieldset>
      {!instances.length ? <p className="simulation-warning">此快照未提取到可用的路由协议事实，请重新导入完整配置后再仿真。</p>
        : endpoints.sourceDeviceId && endpoints.destinationDeviceId && !protocols.length ? <p className="simulation-warning">所选两端没有配置证据支持的共同路由协议。</p> : null}
      {error && <p role="alert" className="simulation-warning">{error}</p>}
      {results.length > 1 && <p className="simulation-note">以下结果按配置中的独立路由范围分别计算，分担比例不跨组相加。</p>}
      {results.map((result, group) => {
        const diagnostics = result.diagnostics.filter(item => !item.ignored);
        const ignored = result.diagnostics.filter(item => item.ignored);
        return <div className="simulation-result" aria-live="polite" key={group}>
          {results.length > 1 && <h3>{protocolName(result.protocol)} · 计算组 {group + 1}{result.destination_address ? ` · ${result.destination_address}` : ""}</h3>}
          {result.status === "ok" ? <>
            <div className="simulation-stats"><div><strong>{result.total_cost}</strong><span>总开销</span></div><div><strong>{result.path_count}</strong><span>等价路径</span></div></div>
            <p className="simulation-note">理论逐跳分担 · 实际 FIB / Hash 可能不同。点击一条路径单独显示，再次点击恢复本组全部路径。</p>
            <div className="simulation-paths">{result.paths.map((path, index) => <button key={index}
              className={`simulation-path ${selected.group === group && selected.path === index ? "active" : ""}`}
              onClick={() => selectPath(group, selected.group === group && selected.path === index ? null : index)}>
              <span><b>路径 {index + 1}</b><strong>{percent(path.share_percent)}</strong></span><span>{path.device_ids.map((id, i) => <span key={`${id}:${i}`}>{i > 0 && <ArrowRight size={12} />}{name(id)}</span>)}</span>
            </button>)}</div>
            {result.paths_truncated && <p className="simulation-note">仅展开前 {result.paths.length} 条，分担占比按完整路径集合计算。</p>}
            <details className="simulation-hop-details"><summary>逐跳出口与配置来源</summary>
              {result.hops.filter(h => selected.group !== group || selected.path === null || result.paths[selected.path].hops.includes(h.id)).map(h => <div className="simulation-hop" key={h.id}>
                <div><b>{name(h.source_device_id)} → {name(h.target_device_id)}</b><strong>{percent(h.share_percent)}</strong></div>
                <div>{ifaceName(h.interface_id)} → {h.next_hop}</div><div>开销 {h.cost} · 本机分担 {percent(h.local_share_percent)}{h.source_refs[0] && <button className="text-button" onClick={() => onSource(h.source_refs[0])}>配置来源</button>}</div>
              </div>)}
            </details>
          </> : <div className="simulation-warning"><b>{{ incomplete: "数据不足，未生成路径", unreachable: "所选两端不可达", unsupported: "当前模型暂不支持" }[result.status]}</b></div>}
          {diagnostics.length > 0 && <details className="simulation-diagnostics" open={result.status !== "ok" || result.forwarding_status === "limit_ambiguous"}><summary>诊断 · {diagnostics.length}</summary>{diagnostics.map((d, i) => <p key={i}>{d.message}{d.interface_id && <button className="text-button" onClick={() => onInterface(d.interface_id!)}>{ifaceName(d.interface_id)} · 接口设置</button>}{d.source_refs?.[0] && <button className="text-button" onClick={() => onSource(d.source_refs![0])}>查看来源</button>}</p>)}</details>}
          {ignored.length > 0 && <details className="simulation-assumptions"><summary>已忽略的配置语义 · {ignored.length}</summary>{ignored.map((d, i) => <p key={i}>{d.message}</p>)}</details>}
          <details className="simulation-assumptions"><summary>计算假设</summary>{result.assumptions.map((a, i) => <p key={i}>{a}</p>)}</details>
        </div>;
      })}
    </div>
  </aside>;
}
