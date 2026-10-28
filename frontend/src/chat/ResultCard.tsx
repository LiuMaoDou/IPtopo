import { ArrowRight, FileText, LocateFixed, Route } from "lucide-react";
import { memo } from "react";
import type { ChatCard, ChatContext, CardAction, ChatEvidence } from "./types";
import type { Ref } from "../types";

const rows = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.filter((row): row is Record<string, unknown> => !!row && typeof row === "object") : [];
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown, fallback = "未知"): string => value === null || value === undefined || value === "" ? fallback : typeof value === "object" ? JSON.stringify(value) : String(value);
const labels: Record<string, string> = { device_count: "设备", interface_count: "接口", link_count: "关系", segment_count: "网段", total_cost: "总开销", path_count: "等价路径", hostname: "设备名称", vendor: "厂商", os: "系统", parse_status: "解析状态", status: "状态", protocol: "协议", scope: "Area / Level" };
const states: Record<string, string> = { ok: "计算完成", incomplete: "数据不足", unreachable: "所选范围不可达", unsupported: "当前不支持", active: "有效", enabled: "启用", disabled: "关闭", unknown: "未知", success: "成功", partial: "部分解析", failed: "解析失败" };
const comparisonStates: Record<string, string> = { consistent: "一致", cost_difference: "开销差异", state_difference: "状态差异", config_only: "仅配置", lsdb_only: "仅 LSDB", port_ambiguous: "端口待关联", config_cost_unknown: "配置开销未知" };

function ResultCard({ card, context, current, onSource, onAction, deviceNames }: {
  card: ChatCard; context: ChatContext; current: boolean; onSource: (ref: Ref) => void; onAction: (action: CardAction) => void; deviceNames: Map<string, string>;
}) {
  const data = card.data || {};
  const topology = object(data.topology);
  const topologyNodes = rows(data.nodes).length ? rows(data.nodes) : rows(topology.nodes);
  const topologyEdges = rows(data.edges).length ? rows(data.edges) : rows(topology.edges);
  const evidence = (card.evidence || []).filter((ref): ref is ChatEvidence => !!ref?.source_file_id && Number.isInteger(ref.line) && ref.line > 0);
  const names = new Map(deviceNames);
  for (const node of topologyNodes) if (node.id) names.set(String(node.id), text(node.label, String(node.id)));
  const name = (id: unknown) => names.get(String(id)) || text(id);
  const countFields = ["device_count", "interface_count", "link_count", "segment_count"].filter(key => typeof data[key] === "number");
  const devices = rows(data.devices);
  const device = object(data.device);
  const interfaces = rows(data.interfaces);
  const paths = rows(data.paths);
  const diagnostics = rows(data.diagnostics);
  const comparisons = rows(data.comparisons);
  const batch = object(data.batch);
  const instances = rows(data.instances);
  const advertisements = rows(data.advertisements).length ? rows(data.advertisements) : rows(data.destinations);
  const sourceLines = rows(data.lines);
  const state = text(data.status, "");
  const layer = data.layer === "lsdb" ? "LSDB 观测" : data.layer === "config" ? "配置推演" : "";
  const locate = (ids?: string[]) => onAction({ card: ids ? { ...card, device_ids: ids, link_ids: [] } : card, context, action: "locate" });
  return <section className={`chat-card chat-card-${card.kind}`} aria-label={card.title}>
    <div className="chat-card-heading"><strong>{card.title}</strong>{layer && <span className="chat-badge">{layer}</span>}</div>
    {Boolean(batch.id) && <p className="chat-card-note">采集：{text(batch.name, text(batch.collector, "未命名批次"))} · {batch.collected_at ? text(batch.collected_at) : "采集时间未知"}</p>}
    {!current && <p className="chat-stale">历史结果 · 项目数据已变化，画布操作已停用</p>}
    {countFields.length > 0 && <div className="chat-card-stats">{countFields.map(key => <div key={key}><b>{text(data[key])}</b><span>{labels[key]}</span></div>)}</div>}
    {card.kind === "devices" && <>
      {devices.length ? <div className="chat-device-list">{devices.slice(0, 20).map((item, index) => <div key={text(item.id, String(index))}>
        <span><b>{text(item.hostname || item.label || item.id)}</b><small>{text(item.vendor, "厂商未知")} · {text(item.interface_count, "?")} 个接口</small></span>
        <button disabled={!current || !item.id} title="在画布定位" aria-label={`定位 ${text(item.hostname || item.id)}`} onClick={() => locate([String(item.id)])}><LocateFixed size={13} /></button>
      </div>)}</div> : <p className="chat-card-note">当前查询没有匹配的设备。</p>}
      {typeof data.total === "number" && <p className="chat-card-note">匹配 {data.total} 台{devices.length < data.total ? `，本次列出 ${devices.length} 台，可继续询问下一页` : ""}。</p>}
    </>}
    {card.kind === "device" && <>
      <dl className="chat-facts">{["hostname", "vendor", "os", "parse_status"].filter(key => device[key] !== undefined).map(key => <div key={key}><dt>{labels[key]}</dt><dd>{states[text(device[key])] || text(device[key])}</dd></div>)}</dl>
      {interfaces.length > 0 && <details open><summary>接口信息 · {text(data.total_interfaces ?? data.total, String(interfaces.length))}</summary><div className="chat-interface-list">{interfaces.slice(0, 12).map((item, index) => <div key={text(item.id, String(index))}><b>{text(item.raw_name || item.name || item.id)}</b><small>{rows(item.addresses).map(address => address.address ? `${address.address}${address.prefix_length === undefined ? "" : `/${address.prefix_length}`}` : "地址未解析").join(" · ") || "没有已解析地址"}</small></div>)}</div>{interfaces.length > 12 && <p className="chat-card-note">显示前 12 个接口，可继续询问指定接口。</p>}</details>}
    </>}
    {card.kind === "topology" && <>
      <p className="chat-card-note">{topologyNodes.length} 个节点 · {topologyEdges.length} 条关系{data.hops !== undefined ? ` · ${text(data.hops)} 跳范围` : ""}</p>
      <div className="chat-device-list">{topologyNodes.filter(node => node.kind !== "segment").slice(0, 12).map((node, index) => <div key={text(node.id, String(index))}><span>{text(node.label || node.id)}</span><button disabled={!current || !node.id} onClick={() => locate([String(node.id)])} aria-label={`定位 ${text(node.label || node.id)}`}><LocateFixed size={13} /></button></div>)}</div>
      {object(data.topology_page).next_offset !== undefined && object(data.topology_page).next_offset !== null && <p className="chat-card-note">当前为部分组网，可继续询问下一页。</p>}
    </>}
    {card.kind === "path" && <>
      {state && <p className={`chat-path-status ${state === "ok" ? "ok" : "warning"}`}>{states[state] || state}</p>}
      {state === "ok" && <>
        <div className="chat-card-stats">{["total_cost", "path_count"].filter(key => data[key] !== undefined).map(key => <div key={key}><b>{text(data[key])}</b><span>{labels[key]}</span></div>)}</div>
        {paths.slice(0, 8).map((path, index) => <div className="chat-path-row" key={index}><b>路径 {index + 1}{typeof path.share_percent === "number" ? ` · ${Number(path.share_percent.toFixed(2))}%` : ""}</b><span>{Array.isArray(path.device_ids) ? path.device_ids.map((id, position) => <span key={position}>{position > 0 && <ArrowRight size={11} />}{name(id)}</span>) : text(path.label, "逐跳信息见计算结果")}</span></div>)}
        <p className="chat-card-note">逐跳理论分担，未验证实际 FIB / Hash。{data.paths_truncated ? "路径列表已截断。" : ""}</p>
        {rows(data.hops).length > 0 && <details><summary>逐跳信息 · {rows(data.hops).length}</summary>{rows(data.hops).slice(0, 20).map((hop, index) => <p className="chat-card-note" key={text(hop.id, String(index))}><b>{name(hop.source_device_id)} → {name(hop.target_device_id)}</b><br />出口：{text(hop.interface_name, name(hop.interface_id))} · 下一跳：{text(hop.next_hop)}<br />开销 {text(hop.cost)}{typeof hop.share_percent === "number" ? ` · 理论流量 ${Number(hop.share_percent.toFixed(2))}%` : ""}</p>)}{data.hops_truncated ? <p className="chat-card-note">逐跳列表已截断，成本与总路径数按完整结果计算。</p> : null}</details>}
      </>}
      {data.forwarding_status === "limit_ambiguous" && <p className="chat-stale">ECMP 上限影响实际下一跳子集。</p>}
      {Array.isArray(data.assumptions) && data.assumptions.length > 0 && <details><summary>计算假设</summary>{data.assumptions.slice(0, 10).map((assumption, index) => <p key={index} className="chat-card-note">{text(assumption)}</p>)}</details>}
    </>}
    {card.kind === "diagnostics" && <>
      {data.protocol ? <p className="chat-card-note">协议：{text(data.protocol).toUpperCase()}{Array.isArray(data.scopes) ? ` · 范围：${data.scopes.map(value => text(value)).join("、") || "无"}` : ""}</p> : null}
      {(instances.length > 0 || topologyNodes.length > 0) && <details open><summary>可选源设备 / 实例 · {instances.length || topologyNodes.length}</summary>{(instances.length ? instances : topologyNodes).slice(0, 12).map((item, index) => <p className="chat-card-note" key={index}>{text(item.hostname || item.label || item.device_id || item.id)}{item.local_name ? ` · 实例 ${text(item.local_name)}` : ""}{item.protocol_id ? ` · ${text(item.protocol_id)}` : ""}</p>)}</details>}
      {advertisements.length > 0 && <details open><summary>可选目的前缀 · {advertisements.length}</summary>{advertisements.slice(0, 12).map((item, index) => <p className="chat-card-note" key={index}>{text(item.hostname || item.origin, name(item.device_id || item.node_id))} · {text(item.prefix || item.address)}</p>)}</details>}
      {sourceLines.length > 0 && <><p className="chat-card-note">{text(data.filename, "来源片段")} · 已脱敏</p><pre className="chat-source-lines">{sourceLines.slice(0, 100).map(item => `${text(item.line, "")}  ${text(item.text, "")}`).join("\n")}</pre></>}
      {!instances.length && !topologyNodes.length && !advertisements.length && !sourceLines.length && !diagnostics.length && <p className="chat-card-note">此查询暂无可用数据，请查看回答中的诊断或补充配置。</p>}
    </>}
    {comparisons.length > 0 && <details open><summary>配置与观测对比 · {comparisons.length}</summary>{comparisons.slice(0, 12).map((item, index) => <p className="chat-card-note" key={index}><b>{name(item.source)} → {name(item.target)}</b><br />{comparisonStates[text(item.status)] || text(item.status)} · 配置 {text(item.config_cost)} / 观测 {text(item.observed_cost)}</p>)}</details>}
    {diagnostics.length > 0 && <details open={state !== "ok" || card.kind === "diagnostics"}><summary>诊断 · {diagnostics.length}</summary>{diagnostics.slice(0, 12).map((item, index) => <p key={index} className="chat-card-note">{item.blocking ? "需补齐：" : ""}{text(item.message || item.code)}</p>)}</details>}
    {card.kind === "summary" && Boolean(data.vendors) && <p className="chat-card-note">厂商：{Array.isArray(data.vendors) ? data.vendors.map(value => text(value)).join("、") : Object.entries(object(data.vendors)).map(([key, value]) => `${key} ${text(value)}`).join("、")}</p>}
    <div className="chat-card-actions">
      {(card.device_ids?.length > 0 || card.link_ids?.length > 0) && <button disabled={!current} onClick={() => locate()}><LocateFixed size={13} />定位</button>}
      {card.highlight && state === "ok" && <button disabled={!current} onClick={() => onAction({ card, context, action: "highlight" })}><Route size={13} />高亮路径</button>}
      {evidence.slice(0, 3).map((ref, index) => <button key={`${ref.source_file_id}:${ref.line}:${index}`} onClick={() => onSource(ref)} title={ref.filename}><FileText size={12} />{ref.label || `来源 ${ref.line}${ref.end_line && ref.end_line !== ref.line ? `–${ref.end_line}` : ""} 行`}</button>)}
    </div>
    {evidence.length > 3 && <details className="chat-more-evidence"><summary>其余 {evidence.length - 3} 处来源</summary>{evidence.slice(3, 30).map((ref, index) => <button key={index} onClick={() => onSource(ref)}>{ref.filename || "配置来源"} · {ref.line} 行</button>)}</details>}
  </section>;
}

export default memo(ResultCard);
