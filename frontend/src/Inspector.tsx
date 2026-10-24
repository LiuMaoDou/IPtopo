import { useState } from "react";
import { segmentIcon } from "./vendorIcons";
import {
  Server,
  X,
  FileText,
  ArrowUpRight,
  Check,
  Minus,
  PenLine,
  Link2,
  StickyNote,
  GitBranch,
  Plus,
} from "lucide-react";
import type {
  Project,
  Selection,
  Ref,
  Mutation,
  Interface,
  Evidence,
} from "./types";
import { statusName } from "./types";
export type Action = {
  type: string;
  target?: Record<string, unknown>;
  initial?: Record<string, string>;
  title?: string;
};
type Props = {
  project: Project;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onSource: (r: Ref) => void;
  onAction: (a: Action) => void;
  onMutate: (m: Mutation) => void;
};
export default function Inspector({
  project,
  selection,
  onSelect,
  onSource,
  onAction,
  onMutate,
}: Props) {
  const [tab, setTab] = useState("interfaces");
  const s = project.snapshot;
  if (!s || !selection)
    return (
      <aside className="inspector empty-inspector">
        <div className="panel-heading">证据检查器</div>
        <div className="empty-detail">
          <Server size={36} />
          <h3>查看每一条关系的依据</h3>
          <p>选择设备、接口或链路，检查配置事实、推断规则与来源行。</p>
          <small>
            IP 同网段仅表示逻辑互联推断；运行状态与物理连接仍需观测证据。
          </small>
        </div>
      </aside>
    );
  if (selection.kind === "bundle") {
    const ids = new Set(selection.linkIds || []);
    const members = s.links.filter((link) => ids.has(link.id) && link.review_state !== "rejected");
    const devices = new Map(s.devices.map((device) => [device.id, device.hostname]));
    const interfaces = new Map(s.interfaces.map((iface) => [iface.id, iface.raw_name]));
    return (
      <aside className="inspector">
        <div className="panel-heading">
          <span>多链路汇总</span>
          <button aria-label="关闭详情" onClick={() => onSelect(null)}><X size={17} /></button>
        </div>
        <div className="inspector-title">
          <Link2 size={31} />
          <div><h2>{members.length} 条互联链路</h2><p>双击画布链路展开 / 收起；点击下方条目查看详情。</p></div>
        </div>
        <div className="inspector-content">
          {members.map((link) => (
            <button className="bundle-member" key={link.id} onClick={() => onSelect({ kind: "link", id: link.id })}>
              <strong>{devices.get(link.device_a_id)} ↔ {devices.get(link.device_b_id)}</strong>
              <span>{interfaces.get(link.interface_a_id || "") || "设备"} ↔ {interfaces.get(link.interface_b_id || "") || "设备"}</span>
              <span>{link.prefixes.join(" · ") || "手工连接"}</span>
              <small>{statusName(link.origin)} · {statusName(link.review_state)}</small>
            </button>
          ))}
          {!members.length && <p>这些链路已不在当前拓扑中。</p>}
        </div>
      </aside>
    );
  }
  const device = s.devices.find((d) => d.id === selection.id),
    link = s.links.find((l) => l.id === selection.id),
    segment = s.segments.find((g) => g.id === selection.id),
    iface = s.interfaces.find((i) => i.id === selection.id);
  const kind = selection.kind;
  const title =
    device?.hostname || iface?.raw_name || segment?.prefix || "链路详情";
  const refs: Ref[] =
    device?.source_refs ||
    iface?.source_refs ||
    link?.evidence.flatMap((e) => e.source_refs) ||
    segment?.evidence.flatMap((e) => e.source_refs) ||
    [];
  const sourceLinks = (items: Ref[]) =>
    items.length ? (
      <div className="source-links">
        {items.map((r, i) => (
          <button key={i} onClick={() => onSource(r)}>
            <FileText size={14} />
            {s.source_files.find((f) => f.id === r.source_file_id)?.filename ||
              "配置来源"}
            <span>
              第 {r.line}
              {r.end_line && r.end_line !== r.line ? "–" + r.end_line : ""} 行
            </span>
            <ArrowUpRight size={13} />
          </button>
        ))}
      </div>
    ) : (
      <p className="empty-note">人工添加的内容没有原始配置行。</p>
    );
  const interfacesById = new Map(s.interfaces.map((item) => [item.id, item]));
  const interfaceName = (id?: string) => id ? interfacesById.get(id)?.raw_name || id : "—";
  const aggregateDetails = (i: Interface) => {
    const parent = interfacesById.get(i.aggregate_interface_id || i.parent_interface_id || "");
    const lag = i.kind === "aggregate" ? i : parent?.kind === "aggregate" ? parent : undefined;
    if (!lag) return null;
    const a = lag.aggregation;
    return <div className="aggregation-details">
      <div>{i.id !== lag.id && <button className="text-button" onClick={() => onSelect({kind: "interface", id: lag.id})}>聚合接口 {lag.raw_name} · </button>}
        {a?.protocol ? `${({lacp: "LACP", static: "静态聚合", pagp: "PAgP"} as Record<string, string>)[a.protocol] || a.protocol} · ${a.mode || "模式未显式配置"}` : "聚合协议未提供"}
        {a?.periodic && ` · ${a.periodic}`}
      </div>
      {(a?.minimum_links !== undefined || a?.link_speed) && <div>
        {a.minimum_links !== undefined && `最少成员 ${a.minimum_links}`}
        {a.link_speed && ` · 成员速率 ${a.link_speed}`}
      </div>}
      <div>配置成员：{lag.aggregate_members?.map((id) => {
        const member = interfacesById.get(id);
        return `${interfaceName(id)}${member?.aggregation?.mode ? ` (${member.aggregation.mode})` : ""}`;
      }).join(", ") || "未提供"}</div>
      <small>协商状态与成员对端：未知</small>
    </div>;
  };
  const interfaceCard = (i: Interface) => (
    <div
      className={
        "interface-card " + (i.effective_state === "inactive" ? "inactive" : "")
      }
      key={i.id}
    >
      <button
        className="interface-name"
        onClick={() => onSelect({ kind: "interface", id: i.id })}
      >
        <strong>{i.raw_name}</strong>
        <span className="badge">{i.kind}</span>
      </button>
      <div className="interface-meta">
        {s.contexts.find((c) => c.id === i.routing_context_id)?.local_name ||
          "default"}{" "}
        · {statusName(i.admin_state)} · 运行态 {statusName(i.oper_state)}
      </div>
      {aggregateDetails(i)}
      {i.addresses.map((a) => (
        <div className="address-row" key={a.id}>
          <span className={"af af" + a.family}>v{a.family}</span>
          <code>
            {a.address
              ? `${a.address}/${a.prefix_length}`
              : a.acquisition_method}
          </code>
          <small>{a.role}</small>
          <button
            title="编辑地址"
            aria-label={"编辑地址 " + a.address}
            onClick={() =>
              onAction({
                type: "override_field",
                target: { kind: "address", id: a.id, field: "address" },
                initial: { field: "address", value: a.address || "" },
                title: "覆盖地址字段",
              })
            }
          >
            <PenLine size={12} />
          </button>
        </div>
      ))}
      {i.description && <p className="description">{i.description}</p>}
      {i.source_refs[0] && (
        <button
          className="text-button"
          onClick={() => onSource(i.source_refs[0])}
        >
          查看来源 · 第 {i.source_refs[0].line} 行 <ArrowUpRight size={12} />
        </button>
      )}
    </div>
  );
  const evidence = (items: Evidence[]) =>
    items.map((e, i) => (
      <div className="evidence-item" key={i}>
        <strong>{e.rule}</strong>
        <dl>
          <dt>地址域</dt>
          <dd>
            {project.domains.domains.find((d) => d.id === e.domain_id)?.name ||
              e.domain_id}
          </dd>
          <dt>前缀</dt>
          <dd>
            <code>{e.prefix}</code>
          </dd>
          <dt>地址族</dt>
          <dd>IPv{e.family}</dd>
        </dl>
        {sourceLinks(e.source_refs)}
      </div>
    ));
  return (
    <aside className="inspector">
      <div className="panel-heading">
        <span>
          {kind === "device"
            ? "设备详情"
            : kind === "interface"
              ? "接口详情"
              : kind === "segment"
                ? "共享网段"
                : "链路详情"}
        </span>
        <button aria-label="关闭详情" onClick={() => onSelect(null)}>
          <X size={17} />
        </button>
      </div>
      <div className="inspector-title">
        {segment ? (
          <img src={segmentIcon} alt="IP 网段" width={31} height={31} />
        ) : (
          <Server size={31} />
        )}
        <div>
          <h2>{title}</h2>
          <p>
            {device
              ? `${device.vendor} · ${device.os}`
              : iface
                ? s.devices.find((d) => d.id === iface.device_id)?.hostname
                : link
                  ? `${statusName(link.origin)} · ${statusName(link.review_state)}`
                  : "共享 IP 网段"}
          </p>
        </div>
      </div>
      {device && (
        <dl className="properties">
          <dt>厂商</dt>
          <dd>{device.vendor || "未指定"}</dd>
          <dt>操作系统</dt>
          <dd>{device.os || "未指定"}</dd>
          <dt>配置格式</dt>
          <dd>{device.format || "人工添加"}</dd>
          <dt>站点</dt>
          <dd>{device.site || "未设置"}</dd>
          <dt>接口数量</dt>
          <dd>
            {s.interfaces.filter((i) => i.device_id === device.id).length}
          </dd>
        </dl>
      )}
      {link && (
        <div className="link-endpoints">
          {[link.interface_a_id, link.interface_b_id].map((id, index) => {
            const i = s.interfaces.find((n) => n.id === id);
            return (
              <div key={index}>
                <small>{index ? "终点" : "起点"}</small>
                <strong>
                  {s.devices.find((d) => d.id === i?.device_id)?.hostname ||
                    link.device_b_id}
                </strong>
                <button
                  className="text-button"
                  onClick={() => i && onSelect({ kind: "interface", id: i.id })}
                >
                  {i?.raw_name || "共享网段"}
                </button>
              </div>
            );
          })}
        </div>
      )}
      <div className="tabs">
        <button
          className={tab === "interfaces" ? "selected" : ""}
          onClick={() => setTab("interfaces")}
        >
          {device || iface ? "接口" : "证据"}
        </button>
        <button
          className={tab === "sources" ? "selected" : ""}
          onClick={() => setTab("sources")}
        >
          来源
        </button>
        <button
          className={tab === "edits" ? "selected" : ""}
          onClick={() => setTab("edits")}
        >
          修正
        </button>
      </div>
      <div className="inspector-content">
        {tab === "sources" ? (
          sourceLinks(refs)
        ) : tab === "edits" ? (
          <>
            <p className="empty-note">
              人工修正独立保存，重新导入时检查有效性。
            </p>
            {project.overrides
              .filter(
                (o) =>
                  JSON.stringify(o.target).includes(selection.id) ||
                  JSON.stringify(o.value).includes(selection.id),
              )
              .map((o) => (
                <div className="edit-row" key={o.id}>
                  <strong>{o.operation}</strong>
                  <span className={"badge " + o.status}>
                    {statusName(o.status)}
                  </span>
                  <p>{o.reason || JSON.stringify(o.value)}</p>
                </div>
              ))}
            <button
              onClick={() =>
                onAction({
                  type: "add_note",
                  target: { kind, id: selection.id },
                  title: "添加备注",
                })
              }
            >
              <StickyNote size={15} />
              添加备注
            </button>
          </>
        ) : device ? (
          s.interfaces
            .filter((i) => i.device_id === device.id)
            .map(interfaceCard)
        ) : iface ? (
          <>
            {interfaceCard(iface)}
            <dl className="properties">
              <dt>规范名称</dt>
              <dd>{iface.normalized_name}</dd>
              <dt>父接口</dt>
              <dd>{interfaceName(iface.parent_interface_id)}</dd>
              <dt>聚合成员</dt>
              <dd>{iface.aggregate_members?.map(interfaceName).join(", ") || "—"}</dd>
              <dt>所属聚合接口</dt>
              <dd>{interfaceName(iface.aggregate_interface_id)}</dd>
              <dt>物理端口</dt>
              <dd>{iface.physical_port_refs?.join(", ") || "—"}</dd>
            </dl>
          </>
        ) : link ? (
          <>
            {[link.interface_a_id, link.interface_b_id].map((id) => interfacesById.get(id)).filter((i): i is Interface => Boolean(i)).map(interfaceCard)}
            {evidence(link.evidence)}
          </>
        ) : segment ? (
          <>
            {evidence(segment.evidence)}
            {segment.interface_ids
              .map((id) => s.interfaces.find((i) => i.id === id))
              .filter(Boolean)
              .map((i) => interfaceCard(i!))}
          </>
        ) : (
          <p>该对象已不在当前拓扑中。</p>
        )}
      </div>
      <div className="inspector-foot">
        {device ? (
          <>
            <button
              className="wide"
              onClick={() =>
                onAction({
                  type: "override_field",
                  target: { kind: "device", id: device.id },
                  initial: { field: "hostname", value: device.hostname },
                  title: "编辑设备",
                })
              }
            >
              <PenLine size={15} />
              编辑设备
            </button>
            <button
              className="wide"
              onClick={() =>
                onAction({
                  type: "add_interface",
                  initial: { device_id: device.id },
                  title: "添加接口",
                })
              }
            >
              <Plus size={15} />
              添加接口
            </button>
          </>
        ) : iface ? (
          <>
            <button
              className="wide"
              onClick={() =>
                onAction({
                  type: "override_field",
                  target: { kind: "interface", id: iface.id },
                  initial: { field: "description", value: iface.description },
                  title: "编辑接口",
                })
              }
            >
              <PenLine size={15} />
              编辑接口
            </button>
            <button
              className="wide"
              onClick={() =>
                onAction({
                  type: "assign_domain",
                  target: { interface_id: iface.id },
                  title: "设置接口地址域",
                })
              }
            >
              映射地址域
            </button>
          </>
        ) : link ? (
          <>
            <div className="button-row">
              <button
                onClick={() =>
                  onMutate({
                    operation: "confirm_link",
                    target: { link_id: link.id },
                    value: {},
                    reason: "界面人工确认",
                  })
                }
              >
                <Check size={15} />
                确认
              </button>
              <button
                className="danger"
                onClick={() =>
                  onMutate({
                    operation: "suppress_link",
                    target: { link_id: link.id },
                    value: {},
                    reason: "界面人工拒绝",
                  })
                }
              >
                <Minus size={15} />
                拒绝
              </button>
            </div>
            <button
              className="wide"
              onClick={() =>
                onAction({
                  type: "rebind_endpoint",
                  target: { link_id: link.id },
                  title: "改接链路端点",
                })
              }
            >
              <Link2 size={15} />
              改接端点
            </button>
          </>
        ) : segment ? (
          <button
            className="wide"
            onClick={() =>
              onAction({
                type: "split_segment",
                target: { segment_id: segment.id },
                title: "拆分共享网段",
              })
            }
          >
            <GitBranch size={15} />
            拆分网段
          </button>
        ) : null}
      </div>
    </aside>
  );
}
