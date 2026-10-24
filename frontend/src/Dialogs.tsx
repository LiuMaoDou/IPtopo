import { useEffect, useRef, useState, useMemo, type ReactNode } from "react";
import { X, Upload, FileText, Plus } from "lucide-react";
import type { Action } from "./Inspector";
import type { Project, Domains } from "./types";
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className={wide ? "modal wide-modal" : "modal"}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === dialog.current) onClose();
      }}
    >
      <div className="modal-heading">
        <h2>{title}</h2>
        <button type="button" aria-label="关闭弹窗" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function SourceDialog({
  source,
  line,
  endLine,
  onClose,
}: {
  source: { filename: string; text: string };
  line: number;
  endLine?: number;
  onClose: () => void;
}) {
  const active = useRef<HTMLDivElement>(null);
  useEffect(() => {
    active.current?.scrollIntoView({ block: "center" });
  }, [source, line]);
  return (
    <Modal title={source.filename} wide onClose={onClose}>
      <p className="source-caption">
        配置来源 · 第 {line}
        {endLine && endLine !== line ? "–" + endLine : ""} 行
      </p>
      <div className="source-code">
        {source.text.split("\n").map((text, i) => (
          <div
            key={i}
            ref={i + 1 === line ? active : undefined}
            className={
              i + 1 >= line && i + 1 <= (endLine || line)
                ? "highlight-line"
                : ""
            }
          >
            <span>{i + 1}</span>
            <code>{text || " "}</code>
          </div>
        ))}
      </div>
    </Modal>
  );
}
export function DomainDialog({
  project,
  onSave,
  onClose,
}: {
  project: Project;
  onSave: (d: Domains) => Promise<void>;
  onClose: () => void;
}) {
  const [domains, setDomains] = useState<Domains>(
      structuredClone(project.domains),
    ),
    [name, setName] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(0);
  const names = useMemo(
    () =>
      new Map(project.snapshot?.devices.map((d) => [d.id, d.hostname]) || []),
    [project.snapshot?.devices],
  );
  const contexts = useMemo(() => {
    const q = query.toLowerCase();
    return (project.snapshot?.contexts || []).filter(
      (c) =>
        !q ||
        (names.get(c.device_id) + " " + c.local_name).toLowerCase().includes(q),
    );
  }, [project.snapshot?.contexts, names, query]);
  const pages = Math.max(1, Math.ceil(contexts.length / 50));
  return (
    <Modal title="管理地址域" wide onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSave(domains);
            onClose();
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="form-note">
          仅把属于同一逻辑网络的上下文映射到共同地址域。默认地址域是一项导入假设。
        </p>
        <div className="domain-create">
          <input
            aria-label="新地址域名称"
            placeholder="新地址域名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            type="button"
            disabled={!name.trim()}
            onClick={() => {
              setDomains({
                ...domains,
                domains: [
                  ...domains.domains,
                  {
                    id: crypto.randomUUID(),
                    name: name.trim(),
                    confirmed: true,
                  },
                ],
              });
              setName("");
            }}
          >
            <Plus size={15} />
            添加
          </button>
        </div>
        <div className="domain-checks">
          {domains.domains.map((d) => (
            <label key={d.id}>
              <input
                type="checkbox"
                checked={d.confirmed}
                onChange={(e) =>
                  setDomains({
                    ...domains,
                    domains: domains.domains.map((n) =>
                      n.id === d.id ? { ...n, confirmed: e.target.checked } : n,
                    ),
                  })
                }
              />
              {d.name} · 已核对
            </label>
          ))}
        </div>
        <div className="domain-pagination">
          <input
            aria-label="筛选上下文"
            placeholder="按设备或上下文筛选"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
          />
          <span>
            {contexts.length} 个上下文 · {page + 1}/{pages}
          </span>
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </button>
          <button
            type="button"
            disabled={page + 1 >= pages}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>设备</th>
                <th>本地上下文</th>
                <th>RD / RT</th>
                <th>映射地址域</th>
              </tr>
            </thead>
            <tbody>
              {contexts.slice(page * 50, (page + 1) * 50).map((c) => (
                <tr key={c.id}>
                  <td>{names.get(c.device_id)}</td>
                  <td>{c.local_name}</td>
                  <td>
                    {c.rd || "—"} / {c.rt?.join(", ") || "—"}
                  </td>
                  <td>
                    <select
                      aria-label={"地址域 " + c.local_name + " " + c.id}
                      value={domains.mappings[c.id] || ""}
                      onChange={(e) => {
                        const mappings = { ...domains.mappings };
                        if (e.target.value) mappings[c.id] = e.target.value;
                        else delete mappings[c.id];
                        setDomains({ ...domains, mappings });
                      }}
                    >
                      <option value="">独立 / 自动默认域</option>
                      {domains.domains.map((d) => (
                        <option value={d.id} key={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {error && <p className="error-box">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary" disabled={busy}>
            保存映射并重算
          </button>
        </div>
      </form>
    </Modal>
  );
}
export default function ActionDialog({
  action,
  project,
  dialects,
  onSubmit,
  onClose,
}: {
  action: Action;
  project: Project | null;
  dialects: { id: string; label: string }[];
  onSubmit: (
    a: Action,
    values: Record<string, string>,
    files: File[],
  ) => Promise<void>;
  onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({
      field: "hostname",
      endpoint: "b",
      strategy: "merge",
      dialect: "",
      ...action.initial,
    }),
    [files, setFiles] = useState<File[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const set = (k: string, v: string) => setValues((s) => ({ ...s, [k]: v }));
  const s = project?.snapshot;
  const field = (
    key: string,
    label: string,
    placeholder = "",
    required = true,
    type = "text",
  ) => (
    <label>
      {label}
      <input
        type={type}
        required={required}
        value={values[key] || ""}
        placeholder={placeholder}
        onChange={(e) => set(key, e.target.value)}
      />
    </label>
  );
  const choose = (
    key: string,
    label: string,
    options: { id: string; name: string }[],
    required = true,
  ) => (
    <label>
      {label}
      <select
        value={values[key] || ""}
        required={required}
        onChange={(e) => set(key, e.target.value)}
      >
        <option value="">请选择</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </label>
  );
  const ifaces = (device?: string) =>
    (s?.interfaces || [])
      .filter((i) => !device || i.device_id === device)
      .map((i) => ({
        id: i.id,
        name: `${s?.devices.find((d) => d.id === i.device_id)?.hostname} / ${i.raw_name} ${i.addresses
          .map((a) => a.address)
          .filter(Boolean)
          .join(", ")}`,
      }));
  return (
    <Modal
      title={
        action.title ||
        {
          import: "导入设备配置",
          project: "新建项目",
          view: "保存命名视图",
          force_link: "添加手工链路",
          add_device: "添加设备",
          restore: "恢复 JSON 项目",
        }[action.type] ||
        "人工修正"
      }
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            await onSubmit(action, values, files);
            onClose();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {(action.type === "import" || action.type === "restore") && (
          <>
            <label
              className="upload-zone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                setFiles(Array.from(e.dataTransfer.files));
              }}
            >
              <Upload size={32} />
              <strong>
                {files.length
                  ? `已选择 ${files.length} 个文件`
                  : "选择文件或拖放到这里"}
              </strong>
              <span>
                {action.type === "restore"
                  ? "IPtopo JSON 项目备份"
                  : "支持设备配置、多文件和 ZIP 压缩包"}
              </span>
              <input
                aria-label="选择导入文件"
                type="file"
                accept={action.type === "restore" ? ".json" : undefined}
                multiple={action.type === "import"}
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
              />
            </label>
            {files.map((f, i) => (
              <div className="chosen-file" key={i}>
                <FileText size={15} />
                <span>{f.name}</span>
                <small>{Math.ceil(f.size / 1024)} KB</small>
              </div>
            ))}
            {action.type === "import" && (
              <>
                <label>
                  解析方言
                  <select
                    value={values.dialect}
                    onChange={(e) => set("dialect", e.target.value)}
                  >
                    <option value="">自动识别</option>
                    {dialects.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  导入策略
                  <select
                    value={values.strategy}
                    onChange={(e) => set("strategy", e.target.value)}
                  >
                    <option value="merge">合并更新 · 保留其他设备</option>
                    <option value="replace">替换快照 · 仅使用本批配置</option>
                  </select>
                </label>
                <p className="form-note">
                  每个文件独立解析；同名文件更新对应设备。人工修正会重放，失效项标记为冲突。
                </p>
              </>
            )}
          </>
        )}
        {action.type === "project" && (
          <>
            {field("name", "项目名称", "例如：园区核心网络")}
            {field("description", "说明", "可选", false)}
          </>
        )}
        {action.type === "view" &&
          field("name", "视图名称", "例如：双栈核心拓扑")}
        {action.type === "add_device" && (
          <>
            {field("hostname", "设备名称")}
            {field("vendor", "厂商", "可选", false)}
            {field("site", "站点", "可选", false)}
          </>
        )}
        {action.type === "add_interface" && (
          <>
            {choose(
              "device_id",
              "所属设备",
              s?.devices.map((d) => ({ id: d.id, name: d.hostname })) || [],
            )}
            {field("raw_name", "接口名称", "例如：GigabitEthernet0/0/1")}
            {field(
              "addresses",
              "IP 地址（逗号分隔 CIDR）",
              "10.0.0.1/30, 2001:db8::1/64",
              false,
            )}
            {choose(
              "kind",
              "接口类型",
              [
                { id: "physical", name: "物理接口" },
                { id: "logical", name: "逻辑接口" },
                { id: "loopback", name: "Loopback" },
                { id: "aggregate", name: "聚合接口" },
              ],
              false,
            )}
          </>
        )}
        {action.type === "force_link" && (
          <>
            {choose("interface_a_id", "起点接口", ifaces(values.device_a_id))}
            {choose("interface_b_id", "终点接口", ifaces(values.device_b_id))}
            <p className="form-note">
              手工关系需要指定明确接口；仅作人工记录，不代表物理连接已被观测。
            </p>
          </>
        )}
        {action.type === "rebind_endpoint" && (
          <>
            {choose("endpoint", "修改端点", [
              { id: "a", name: "起点 A" },
              { id: "b", name: "终点 B" },
            ])}
            {choose("interface_id", "改接到接口", ifaces())}
          </>
        )}
        {action.type === "assign_domain" &&
          choose(
            "domain_id",
            "地址域",
            project?.domains.domains.map((d) => ({ id: d.id, name: d.name })) ||
              [],
          )}
        {action.type === "override_field" && (
          <>
            {choose(
              "field",
              "覆盖字段",
              (action.target?.kind === "device"
                ? ["hostname", "vendor", "os", "site"]
                : action.target?.kind === "address"
                  ? ["address", "prefix_length", "role", "effective_state"]
                  : [
                      "raw_name",
                      "description",
                      "admin_state",
                      "effective_state",
                    ]
              ).map((f) => ({ id: f, name: f })),
            )}
            {field("value", "新值", "", false)}
            <p className="form-note">
              原始解析事实保留。重新导入时若原值变化，将提示冲突。
            </p>
          </>
        )}
        {action.type === "split_segment" && (
          <>
            <p className="form-note">
              为每个接口填写分组名。相同名称归为同组；不同名称被隔离。
            </p>
            {s?.segments
              .find((g) => g.id === action.target?.segment_id)
              ?.interface_ids.map((id) => (
                <div key={id}>
                  {field(
                    "split:" + id,
                    ifaces().find((i) => i.id === id)?.name || id,
                    "分组名称",
                  )}
                </div>
              ))}
          </>
        )}
        {action.type === "add_note" && (
          <label>
            备注
            <textarea
              required
              value={values.text || ""}
              onChange={(e) => set("text", e.target.value)}
              rows={4}
            />
          </label>
        )}
        {!["import", "restore", "project", "view"].includes(action.type) && (
          <label>
            修改原因
            <textarea
              value={values.reason || ""}
              onChange={(e) => set("reason", e.target.value)}
              rows={2}
              placeholder="帮助后续审阅（可选）"
            />
          </label>
        )}
        {error && (
          <p className="error-box" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            className="primary"
            disabled={
              busy ||
              (["import", "restore"].includes(action.type) && !files.length)
            }
          >
            {busy ? "处理中…" : action.type === "import" ? "开始导入" : "保存"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
