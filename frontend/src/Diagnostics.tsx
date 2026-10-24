import { useState } from "react";
import {
  AlertTriangle,
  ChevronUp,
  ChevronDown,
  Undo2,
  RotateCcw,
  FileText,
} from "lucide-react";
import type { Project, Ref, Selection } from "./types";
import { statusName } from "./types";
export default function Diagnostics({
  project,
  onSource,
  onSelect,
  onOverride,
  onHistory,
}: {
  project: Project;
  onSource: (r: Ref) => void;
  onSelect: (s: Selection) => void;
  onOverride: (id: string, status: string) => void;
  onHistory: (id: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [tab, setTab] = useState("quality");
  const s = project.snapshot;
  const diagnostics = s?.diagnostics || [];
  return (
    <section className={"diagnostics " + (open ? "expanded" : "")}>
      <div className="diagnostics-heading">
        <div className="tabs">
          <button
            className={tab === "quality" ? "selected" : ""}
            onClick={() => {
              setTab("quality");
              setOpen(true);
            }}
          >
            质量报告 <small>{diagnostics.length}</small>
          </button>
          <button
            className={tab === "edits" ? "selected" : ""}
            onClick={() => {
              setTab("edits");
              setOpen(true);
            }}
          >
            修改记录 <small>{project.overrides.length}</small>
            {project.overrides.some((o) => o.status === "conflict") && (
              <i className="conflict-dot" />
            )}
          </button>
          <button
            className={tab === "unmatched" ? "selected" : ""}
            onClick={() => {
              setTab("unmatched");
              setOpen(true);
            }}
          >
            未匹配 <small>{s?.unmatched.length || 0}</small>
          </button>
          <button
            className={tab === "history" ? "selected" : ""}
            onClick={() => {
              setTab("history");
              setOpen(true);
            }}
          >
            配置快照
          </button>
        </div>
        <button
          aria-label={open ? "收起诊断" : "展开诊断"}
          onClick={() => setOpen(!open)}
        >
          {open ? <ChevronDown size={17} /> : <ChevronUp size={17} />}
        </button>
      </div>
      {open && (
        <div className="diagnostics-content table-scroll">
          {tab === "quality" ? (
            diagnostics.length ? (
              <table>
                <thead>
                  <tr>
                    <th>级别</th>
                    <th>类型</th>
                    <th>详情</th>
                    <th>来源</th>
                  </tr>
                </thead>
                <tbody>
                  {diagnostics.map((d, i) => (
                    <tr key={d.id || i}>
                      <td>
                        <span className={"severity " + d.severity}>
                          <AlertTriangle size={13} />
                          {d.severity === "error"
                            ? "错误"
                            : d.severity === "warning"
                              ? "警告"
                              : "提示"}
                        </span>
                      </td>
                      <td>
                        <code>{d.code}</code>
                      </td>
                      <td>
                        <button
                          className="cell-button"
                          onClick={() =>
                            d.interface_id
                              ? onSelect({
                                  kind: "interface",
                                  id: d.interface_id,
                                })
                              : d.device_id &&
                                onSelect({ kind: "device", id: d.device_id })
                          }
                        >
                          {d.message}
                        </button>
                      </td>
                      <td>
                        {d.source_file_id ? (
                          <button
                            className="text-button"
                            onClick={() =>
                              onSource({
                                source_file_id: d.source_file_id!,
                                line: d.line || 1,
                              })
                            }
                          >
                            <FileText size={13} />第 {d.line || 1} 行
                          </button>
                        ) : (
                          "全局"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="empty-note">当前没有解析或推断诊断。</p>
            )
          ) : tab === "edits" ? (
            project.overrides.length ? (
              <table>
                <thead>
                  <tr>
                    <th>状态</th>
                    <th>操作</th>
                    <th>原因 / 内容</th>
                    <th>时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {[...project.overrides].reverse().map((o) => (
                    <tr key={o.id}>
                      <td>
                        <span className={"badge " + o.status}>
                          {statusName(o.status)}
                        </span>
                      </td>
                      <td>{o.operation}</td>
                      <td>
                        {o.conflict_reason ||
                          o.conflict ||
                          o.reason ||
                          JSON.stringify(o.value)}
                        {o.status === "conflict" && (
                          <details>
                            <summary>原值 / 当前值 / 请求值</summary>
                            <pre>
                              {JSON.stringify(
                                {
                                  baseline: (o as any).baseline,
                                  current_value: (o as any).current_value,
                                  requested: o.value,
                                },
                                null,
                                2,
                              )}
                            </pre>
                          </details>
                        )}
                      </td>
                      <td>{new Date(o.created_at).toLocaleString()}</td>
                      <td>
                        <button
                          onClick={() =>
                            onOverride(
                              o.id,
                              o.status === "active" ? "undone" : "active",
                            )
                          }
                        >
                          {o.status === "active" ? (
                            <Undo2 size={13} />
                          ) : (
                            <RotateCcw size={13} />
                          )}{" "}
                          {o.status === "active" ? "撤销" : "重新应用"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="empty-note">尚无人工修改。</p>
            )
          ) : tab === "unmatched" ? (
            <table>
              <thead>
                <tr>
                  <th>未匹配对象</th>
                  <th>排除原因 / 说明</th>
                </tr>
              </thead>
              <tbody>
                {(s?.unmatched || []).map((item: any, i) => (
                  <tr key={i}>
                    <td>
                      {item.interface_id ? (
                        <button
                          onClick={() =>
                            onSelect({
                              kind: "interface",
                              id: item.interface_id,
                            })
                          }
                        >
                          {s?.interfaces.find((n) => n.id === item.interface_id)
                            ?.raw_name || item.interface_id}
                        </button>
                      ) : (
                        item.id || "—"
                      )}
                    </td>
                    <td>
                      {item.reason || item.message || JSON.stringify(item)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>快照</th>
                  <th>创建时间</th>
                  <th>比较</th>
                </tr>
              </thead>
              <tbody>
                {project.snapshots.map((snap, i) => (
                  <tr key={snap.id}>
                    <td>
                      配置快照 {project.snapshots.length - i}
                      {snap.id === project.snapshot_id ? " · 当前" : ""}
                    </td>
                    <td>{new Date(snap.created_at).toLocaleString()}</td>
                    <td>
                      <button onClick={() => onHistory(snap.id)}>
                        与当前比较
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
