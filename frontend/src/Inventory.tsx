import { useState, useMemo, useRef, useEffect } from "react";
import {
  Server,
  FileText,
  Plus,
  Search,
  ChevronRight,
  Globe2,
  CheckCircle2,
} from "lucide-react";
import type { Project, Selection, Ref, Device } from "./types";
import { statusName } from "./types";
export default function Inventory({
  devices,
  project,
  selection,
  onSelect,
  search,
  setSearch,
  onSource,
  onAction,
}: {
  devices: Device[];
  project: Project;
  selection: Selection;
  onSelect: (s: Selection) => void;
  search: string;
  setSearch: (s: string) => void;
  onSource: (r: Ref) => void;
  onAction: (s: string) => void;
}) {
  const [tab, setTab] = useState("devices"),
    [scrollTop, setScrollTop] = useState(0),
    [height, setHeight] = useState(600);
  const listRef = useRef<HTMLDivElement>(null);
  const s = project.snapshot;
  const names = useMemo(
    () => new Map(s?.devices.map((d) => [d.id, d.hostname]) || []),
    [s?.devices],
  );
  const domainNames = useMemo(
    () => new Map(project.domains.domains.map((d) => [d.id, d.name])),
    [project.domains.domains],
  );
  const files = s?.source_files || [],
    contexts = s?.contexts || [];
  const count =
    tab === "devices"
      ? devices.length
      : tab === "files"
        ? files.length
        : contexts.length;
  const rowHeight = tab === "files" ? 88 : 68,
    windowed = count > 150,
    visible = Math.ceil(height / rowHeight) + 10;
  const start = windowed
    ? Math.max(
        0,
        Math.min(count - visible, Math.floor(scrollTop / rowHeight) - 5),
      )
    : 0;
  const end = windowed ? Math.min(count, start + visible) : count;
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setHeight(el.clientHeight));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0;
    setScrollTop(0);
  }, [search, tab, count]);
  const style = (index: number) =>
    windowed
      ? {
          position: "absolute" as const,
          top: index * rowHeight,
          left: 0,
          height: rowHeight - 4,
          width: "100%",
        }
      : undefined;
  const rows = [];
  for (let index = start; index < end; index++) {
    if (tab === "devices") {
      const d = devices[index];
      rows.push(
        <button
          key={d.id}
          style={style(index)}
          className={"device-row " + (selection?.id === d.id ? "selected" : "")}
          onClick={() => onSelect({ kind: "device", id: d.id })}
        >
          <span className="device-icon">
            <Server size={23} />
          </span>
          <span>
            <strong>{d.hostname}</strong>
            <small>
              {d.vendor} · {d.os}
            </small>
          </span>
          <ChevronRight size={14} />
        </button>,
      );
    } else if (tab === "files") {
      const f = files[index];
      rows.push(
        <button
          key={f.id}
          style={style(index)}
          className="file-row"
          onClick={() => onSource({ source_file_id: f.id, line: 1 })}
        >
          <FileText size={19} />
          <span>
            <strong>{f.filename}</strong>
            <small>
              {statusName(f.parse_status)} · {f.interface_count} 接口
            </small>
            <small>
              IPv4 {f.ipv4_count} / IPv6 {f.ipv6_count}
            </small>
          </span>
        </button>,
      );
    } else {
      const c = contexts[index];
      rows.push(
        <div key={c.id} style={style(index)} className="context-row">
          <strong>{names.get(c.device_id)}</strong>
          <span>{c.local_name}</span>
          <small>
            {domainNames.get(project.domains.mappings[c.id]) || "独立地址域"}
          </small>
        </div>,
      );
    }
  }
  return (
    <aside className="inventory">
      <div className="tabs">
        <button
          className={tab === "devices" ? "selected" : ""}
          onClick={() => setTab("devices")}
        >
          设备 <small>{s?.devices.length || 0}</small>
        </button>
        <button
          className={tab === "files" ? "selected" : ""}
          onClick={() => setTab("files")}
        >
          文件
        </button>
        <button
          className={tab === "domains" ? "selected" : ""}
          onClick={() => setTab("domains")}
        >
          地址域
        </button>
      </div>
      <div className="search">
        <Search size={16} />
        <input
          aria-label="搜索设备、接口或地址"
          placeholder="搜索设备、接口或地址"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      {tab === "domains" && (
        <div className="inventory-domain-summary">
          <p className="empty-note">
            同名 VRF 不会自动合并；请确认跨设备的地址域映射。
          </p>
          {project.domains.domains.map((d) => (
            <div className="domain-row" key={d.id}>
              <Globe2 size={19} />
              <span>
                <strong>{d.name}</strong>
                <small>{d.confirmed ? "已确认" : "导入假设 · 待核对"}</small>
              </span>
              {d.confirmed && <CheckCircle2 size={15} />}
            </div>
          ))}
        </div>
      )}
      <div
        className="inventory-list"
        ref={listRef}
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        {count ? (
          <div
            className={windowed ? "virtual-inventory-list" : ""}
            style={
              windowed
                ? { height: count * rowHeight, position: "relative" }
                : undefined
            }
          >
            {rows}
          </div>
        ) : (
          <p className="empty-note">
            {search ? "没有匹配的设备" : "导入配置后，设备将在此显示。"}
          </p>
        )}
      </div>
      <div className="inventory-foot">
        <button
          className="wide"
          onClick={() =>
            onAction(
              tab === "domains"
                ? "domains"
                : tab === "files"
                  ? "import"
                  : "add_device",
            )
          }
        >
          <Plus size={16} />
          {tab === "domains"
            ? "管理地址域"
            : tab === "files"
              ? "导入配置"
              : "添加设备"}
        </button>
      </div>
    </aside>
  );
}
