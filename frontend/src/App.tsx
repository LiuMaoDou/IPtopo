import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Network,
  Upload,
  Download,
  Plus,
  CheckCircle2,
  Save,
  LayoutGrid,
  GitBranch,
  ChevronDown,
  RefreshCw,
  FolderOpen,
  X,
  Link2,
  PanelLeft,
  PanelRight,
  Play,
  AlertCircle,
} from "lucide-react";
import Graph, { type GraphHandle } from "./Graph";
import { indexTopology, filterTopology } from "./graphModel";
import Inventory from "./Inventory";
import Inspector, { type Action } from "./Inspector";
import ActionDialog, { DomainDialog, Modal, SourceDialog } from "./Dialogs";
import Diagnostics from "./Diagnostics";
import {
  api,
  blankView,
  statusName,
  type Project,
  type Summary,
  type Selection,
  type Ref,
  type Mutation,
  type View,
  type GraphData,
  type Job,
  type Snapshot,
} from "./types";
const initialFilters = {
  search: "",
  family: "all",
  vendor: "all",
  domain: "all",
  mode: "device",
  focus: "",
};
export default function App() {
  const [projects, setProjects] = useState<Summary[]>([]),
    [project, setProject] = useState<Project | null>(null),
    [capabilities, setCapabilities] = useState<{
      dialects: { id: string; label: string }[];
    }>({ dialects: [] }),
    [selection, setSelection] = useState<Selection>(null),
    [action, setAction] = useState<Action | null>(null),
    [domainsOpen, setDomainsOpen] = useState(false),
    [source, setSource] = useState<
      | ({ filename: string; text: string } & {
          line: number;
          endLine?: number;
        })
      | null
    >(null),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false),
    [job, setJob] = useState<Job | null>(null),
    [view, setView] = useState<View>(blankView()),
    [filters, setFilters] = useState(initialFilters),
    [layout, setLayout] = useState("fcose"),
    [onlySelected, setOnlySelected] = useState(false),
    [mobilePanel, setMobilePanel] = useState("canvas"),
    [saved, setSaved] = useState(true),
    [exportOpen, setExportOpen] = useState(false),
    [viewSettings, setViewSettings] = useState(false),
    [includeSources, setIncludeSources] = useState(false),
    [history, setHistory] = useState<{
      title: string;
      items: {
        kind: string;
        added: number;
        removed: number;
        changed: number;
      }[];
    } | null>(null);
  const graph = useRef<GraphHandle>(null),
    projectRef = useRef(project),
    viewRef = useRef(view),
    queue = useRef<Promise<unknown>>(Promise.resolve()),
    saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    modeRef = useRef("device"),
    loadToken = useRef(0),
    pollToken = useRef(0);
  projectRef.current = project;
  viewRef.current = view;
  modeRef.current = filters.mode;
  const notify = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(""), 3500);
  };
  const updateProject = (p: Project) => {
    projectRef.current = p;
    setProject(p);
    setProjects((prev) =>
      prev.some((n) => n.id === p.id)
        ? prev.map((n) =>
            n.id === p.id
              ? {
                  ...n,
                  name: p.name,
                  device_count: p.snapshot?.devices.length || 0,
                }
              : n,
          )
        : [
            ...prev,
            {
              id: p.id,
              name: p.name,
              device_count: p.snapshot?.devices.length || 0,
            },
          ],
    );
  };
  const loadProject = async (id: string, mode?: string) => {
    const token = ++loadToken.current;
    setBusy(true);
    setError("");
    try {
      const p = await api<Project>(
        `/api/projects/${id}?mode=${mode || modeRef.current}`,
      );
      if (token !== loadToken.current) return;
      updateProject(p);
      setSelection(null);
      if (!mode) {
        const v = p.views[0] || blankView();
        setView(v);
        viewRef.current = v;
        const f = { ...initialFilters, ...v.filters };
        setFilters(f);
        if (f.mode !== (mode || modeRef.current)) {
          const corrected = await api<Project>(
            `/api/projects/${id}?mode=${f.mode}`,
          );
          if (token === loadToken.current) updateProject(corrected);
        }
      }
      const running = p.jobs?.find((j) =>
        ["running", "queued", "pending", "cancelling"].includes(j.status),
      );
      if (running) setJob(running);
      else setJob(null);
      setSaved(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let active = true;
    Promise.all([
      api<Summary[]>("/api/projects"),
      api<{ dialects: { id: string; label: string }[] }>("/api/capabilities"),
    ])
      .then(([ps, c]) => {
        if (!active) return;
        setProjects(ps);
        setCapabilities(c);
        const remembered = localStorage.getItem("iptopo.project");
        const id = ps.find((p) => p.id === remembered)?.id || ps[0]?.id;
        if (id) void loadProject(id);
      })
      .catch((e) => setError(String(e)));
    return () => {
      active = false;
      pollToken.current++;
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, []);
  useEffect(() => {
    if (project) localStorage.setItem("iptopo.project", project.id);
  }, [project?.id]);
  const mutate = useCallback(
    (
      path: string,
      body: Record<string, unknown>,
      method = "POST",
    ): Promise<Project> => {
      const id = projectRef.current?.id;
      if (!id) return Promise.reject(new Error("请先创建项目"));
      const task = queue.current
        .catch(() => {})
        .then(async () => {
          if (projectRef.current?.id !== id)
            throw new Error("项目已切换，请重新执行");
          const p = await api<Project>(`/api/projects/${id}${path}`, {
            method,
            body: JSON.stringify({
              ...body,
              version: projectRef.current.version,
            }),
          });
          if (modeRef.current === "segment") {
            const corrected = await api<Project>(
              `/api/projects/${id}?mode=segment`,
            );
            updateProject(corrected);
            return corrected;
          }
          updateProject(p);
          return p;
        });
      queue.current = task;
      return task;
    },
    [],
  );
  const showError = (e: unknown) =>
    setError(e instanceof Error ? e.message : String(e));
  const checkOverride = (p: Project) => {
    const latest = p.overrides[p.overrides.length - 1];
    if (latest?.status === "conflict")
      throw new Error(
        latest.conflict ||
          latest.conflict_reason ||
          "修正存在冲突，请查看修改记录并调整",
      );
  };
  const mutateSafe = (m: Mutation) => {
    void mutate("/overrides", m)
      .then((p) => {
        checkOverride(p);
        notify("人工修正已保存");
      })
      .catch(showError);
  };
  const saveView = useCallback(async (v: View) => {
    const id = projectRef.current?.id;
    if (!id) return;
    setSaved(false);
    const task = queue.current
      .catch(() => {})
      .then(async () => {
        const current = projectRef.current;
        if (!current || current.id !== id) return;
        const summary = await api<{
          id: string;
          version: number;
          views: View[];
        }>(
          `/api/projects/${id}/views/${encodeURIComponent(v.id)}?summary=true`,
          {
            method: "PUT",
            body: JSON.stringify({ version: current.version, view: v }),
          },
        );
        if (projectRef.current?.id === id) {
          updateProject({
            ...projectRef.current,
            version: summary.version,
            views: summary.views,
          });
          setSaved(true);
        }
      });
    queue.current = task;
    await task;
  }, []);
  const viewChanged = (partial: Partial<View>) => {
    const updated = { ...viewRef.current, ...partial };
    viewRef.current = updated;
    setView(updated);
    setSaved(false);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const projectId = projectRef.current?.id;
    saveTimer.current = setTimeout(() => {
      if (
        projectRef.current?.id === projectId &&
        (!job ||
          !["running", "queued", "pending", "cancelling"].includes(job.status))
      )
        void saveView(updated).catch(showError);
    }, 1000);
  };
  const flushView = async () => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    if (
      projectRef.current?.snapshot &&
      !["queued", "running", "cancelling"].includes(job?.status || "")
    ) {
      const next = { ...viewRef.current, ...graph.current?.capture() };
      await saveView(next);
    }
  };
  const setFilter = (key: string, value: string) => {
    const f = { ...filters, [key]: value };
    setFilters(f);
    viewChanged({ ...graph.current?.capture(), filters: f });
    if (key === "mode" && project) {
      void loadProject(project.id, value);
    }
  };
  const ensureProject = async () => {
    if (projectRef.current) return projectRef.current;
    const p = await api<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name: "我的网络" }),
    });
    updateProject(p);
    return p;
  };
  useEffect(() => {
    if (
      !job ||
      !["running", "queued", "pending", "cancelling"].includes(job.status)
    )
      return;
    const token = ++pollToken.current;
    const id = projectRef.current?.id;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const j = await api<Job>(`/api/imports/${job.id}`);
        if (token !== pollToken.current) return;
        setJob(j);
        if (
          [
            "completed",
            "complete",
            "failed",
            "cancelled",
            "succeeded",
            "partial",
            "interrupted",
            "conflict",
          ].includes(j.status)
        ) {
          if (projectRef.current?.id === id) {
            const p = await api<Project>(
              `/api/projects/${id}?mode=${modeRef.current}`,
            );
            updateProject(p);
            notify(
              j.status === "failed" ? "导入失败，请查看结果" : "导入处理完成",
            );
          }
          return;
        }
        timer = setTimeout(poll, 800);
      } catch (e) {
        showError(e);
        timer = setTimeout(poll, 2500);
      }
    };
    timer = setTimeout(poll, 500);
    return () => {
      clearTimeout(timer);
      pollToken.current++;
    };
  }, [job?.id]);
  const openSource = async (r: Ref) => {
    if (!project) return;
    try {
      const text = await api<{ filename: string; text: string }>(
        `/api/projects/${project.id}/sources/${r.source_file_id}`,
      );
      setSource({ ...text, line: r.line, endLine: r.end_line });
    } catch (e) {
      showError(e);
    }
  };
  const submit = async (
    a: Action,
    v: Record<string, string>,
    files: File[],
  ) => {
    if (["project", "restore", "import"].includes(a.type)) await flushView();
    if (a.type === "project") {
      const p = await api<Project>("/api/projects", {
        method: "POST",
        body: JSON.stringify({ name: v.name, description: v.description }),
      });
      updateProject(p);
      setSelection(null);
      setView(blankView());
      setFilters(initialFilters);
      notify("项目已创建");
      return;
    }
    if (a.type === "import") {
      const p = await ensureProject();
      await queue.current.catch(() => {});
      const f = new FormData();
      files.forEach((file) => f.append("files", file));
      if (v.dialect) f.append("dialect", v.dialect);
      f.append("strategy", v.strategy);
      f.append(
        "expected_version",
        String(projectRef.current?.version || p.version),
      );
      setJob(
        await api<Job>(`/api/projects/${p.id}/imports`, {
          method: "POST",
          body: f,
        }),
      );
      return;
    }
    if (a.type === "restore") {
      const f = new FormData();
      f.append("file", files[0]);
      const p = await api<Project>("/api/projects/restore", {
        method: "POST",
        body: f,
      });
      await loadProject(p.id);
      notify("项目已恢复");
      return;
    }
    if (a.type === "view") {
      const next = {
        ...viewRef.current,
        ...graph.current?.capture(),
        id: crypto.randomUUID(),
        name: v.name,
        filters,
      };
      await saveView(next);
      setView(next);
      return;
    }
    let target = a.target || {},
      value: Record<string, unknown> = {};
    switch (a.type) {
      case "add_device":
        value = { hostname: v.hostname, vendor: v.vendor, site: v.site };
        break;
      case "add_interface": {
        const addresses = (v.addresses || "")
          .split(",")
          .map((a) => a.trim())
          .filter(Boolean)
          .map((address) => {
            const [ip, prefix] = address.split("/");
            if (prefix === undefined || !/^\d+$/.test(prefix))
              throw new Error("每个地址需要 CIDR 前缀，例如 10.0.0.1/30");
            return {
              address: ip,
              prefix_length: Number(prefix),
              family: ip.includes(":") ? 6 : 4,
            };
          });
        value = {
          device_id: v.device_id,
          raw_name: v.raw_name,
          kind: v.kind || "physical",
          addresses,
        };
        break;
      }
      case "force_link":
        if (v.interface_a_id === v.interface_b_id)
          throw new Error("请选择两个不同接口");
        value = {
          interface_a_id: v.interface_a_id,
          interface_b_id: v.interface_b_id,
        };
        break;
      case "rebind_endpoint":
        value = { endpoint: v.endpoint, interface_id: v.interface_id };
        break;
      case "assign_domain":
        value = { domain_id: v.domain_id };
        break;
      case "override_field":
        target = { ...target, field: v.field };
        value = {
          value: v.field === "prefix_length" ? Number(v.value) : v.value,
        };
        break;
      case "split_segment":
        value = {
          assignments: Object.fromEntries(
            Object.entries(v)
              .filter(([k]) => k.startsWith("split:"))
              .map(([k, val]) => [k.slice(6), val]),
          ),
        };
        break;
      case "add_note":
        value = { text: v.text };
        break;
      default:
        throw new Error("未知操作");
    }
    const changed = await mutate("/overrides", {
      operation: a.type,
      target,
      value,
      reason: v.reason || "",
    });
    checkOverride(changed);
    notify("人工修正已保存");
  };
  const demo = async () => {
    try {
      setBusy(true);
      const p = await ensureProject();
      setJob(
        await api<Job>(`/api/projects/${p.id}/demo`, {
          method: "POST",
          body: "{}",
        }),
      );
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  };
  const openAction = (a: Action) => {
    setAction(a);
    setMobilePanel("canvas");
  };
  const topologyIndex = useMemo(
    () =>
      project?.snapshot
        ? indexTopology(project.snapshot, project.domains)
        : null,
    [project?.snapshot, project?.domains],
  );
  const inventoryDevices = useMemo(() => {
    const needle = filters.search.toLowerCase();
    return (project?.snapshot?.devices || []).filter(
      (d) => !needle || topologyIndex?.search.get(d.id)?.includes(needle),
    );
  }, [project?.snapshot, topologyIndex, filters.search]);
  const filtered = useMemo<GraphData>(
    () =>
      project && topologyIndex
        ? filterTopology(project.topology, topologyIndex, filters)
        : { nodes: [], edges: [] },
    [project?.topology, topologyIndex, filters],
  );
  useEffect(() => {
    if ((project?.topology.nodes.length || 0) > 150 && layout === "fcose")
      setLayout("components");
  }, [project?.topology.nodes.length]);
  const exportFile = async (format: string) => {
    setExportOpen(false);
    try {
      if (format === "png") {
        graph.current?.png();
        return;
      }
      if (!project) return;
      const latest = {
        ...viewRef.current,
        ...graph.current?.capture(),
        filters,
      };
      await saveView(latest);
      const a = document.createElement("a");
      a.href = `/api/projects/${project.id}/exports/${format}?view_id=${encodeURIComponent(latest.id)}&include_sources=${includeSources}&mode=${filters.mode}`;
      a.download = "";
      a.click();
    } catch (e) {
      showError(e);
    }
  };
  const compareHistory = async (id: string) => {
    if (!project?.snapshot) return;
    try {
      const result = await api<Snapshot & { snapshot?: Snapshot }>(
        `/api/projects/${project.id}/snapshots/${id}`,
      );
      const prev = result.snapshot || result,
        current = project.snapshot;
      const items = (
        ["devices", "interfaces", "links", "segments"] as const
      ).map((kind) => {
        const old = new Map((prev[kind] || []).map((x) => [x.id, x])),
          now = new Map(current[kind].map((x) => [x.id, x]));
        return {
          kind: {
            devices: "设备",
            interfaces: "接口",
            links: "关系",
            segments: "网段",
          }[kind],
          added: [...now.keys()].filter((k) => !old.has(k)).length,
          removed: [...old.keys()].filter((k) => !now.has(k)).length,
          changed: [...now.keys()].filter(
            (k) =>
              old.has(k) &&
              JSON.stringify(old.get(k)) !== JSON.stringify(now.get(k)),
          ).length,
        };
      });
      setHistory({ title: "配置快照与当前比较", items });
    } catch (e) {
      showError(e);
    }
  };
  const importing =
    !!job &&
    ["running", "queued", "pending", "cancelling"].includes(job.status);
  const activeViewOptions = project?.views.some((v) => v.id === view.id)
    ? project.views
    : [view, ...(project?.views || [])];
  return (
    <div className={"app-shell panel-" + mobilePanel}>
      <header className="app-header">
        <a className="brand" href="/" aria-label="IPtopo 首页">
          <Network size={29} />
          <span>IPtopo</span>
        </a>
        <div className="project-switch">
          <FolderOpen size={16} />
          <select
            aria-label="切换项目"
            value={project?.id || ""}
            onChange={(e) => {
              const id = e.target.value;
              void flushView()
                .then(() => loadProject(id))
                .catch(showError);
            }}
          >
            <option value="" disabled>
              选择项目
            </option>
            {projects.map((p) => (
              <option value={p.id} key={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button
            aria-label="新建项目"
            title="新建项目"
            onClick={() => openAction({ type: "project" })}
          >
            <Plus size={16} />
          </button>
        </div>
        <span className="save-status">
          <CheckCircle2 size={14} />
          {saved ? "已保存" : "保存中…"}
        </span>
        <div className="header-spacer" />
        <button
          className="restore-button"
          onClick={() => openAction({ type: "restore" })}
        >
          <FolderOpen size={16} />
          恢复项目
        </button>
        <button
          className="primary"
          onClick={() => openAction({ type: "import" })}
          disabled={importing}
        >
          <Upload size={16} />
          导入配置
        </button>
        <div className="export-wrap">
          <button
            disabled={!project?.snapshot}
            onClick={() => setExportOpen(!exportOpen)}
          >
            <Download size={16} />
            导出
            <ChevronDown size={13} />
          </button>
          {exportOpen && (
            <div className="dropdown">
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={includeSources}
                  onChange={(e) => setIncludeSources(e.target.checked)}
                />
                JSON 包含原始配置
              </label>
              {[
                ["json", "项目 JSON"],
                ["png", "拓扑 PNG"],
                ["csv", "数据 CSV"],
                ["drawio", "可编辑 draw.io"],
              ].map(([format, label]) => (
                <button
                  key={format}
                  title={
                    format === "png"
                      ? "完整拓扑 PNG，最长边不超过 4096 像素"
                      : undefined
                  }
                  onClick={() => void exportFile(format)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </header>
      <div className="toolbar">
        <div className="segmented">
          <button
            className={filters.mode === "device" ? "active" : ""}
            onClick={() => setFilter("mode", "device")}
          >
            <LayoutGrid size={16} />
            设备视图
          </button>
          <button
            className={filters.mode === "segment" ? "active" : ""}
            onClick={() => setFilter("mode", "segment")}
          >
            <GitBranch size={16} />
            网段视图
          </button>
        </div>
        <select
          aria-label="地址族筛选"
          value={filters.family}
          onChange={(e) => setFilter("family", e.target.value)}
        >
          <option value="all">IPv4 + IPv6</option>
          <option value="4">仅 IPv4</option>
          <option value="6">仅 IPv6</option>
        </select>
        <select
          aria-label="厂商筛选"
          value={filters.vendor}
          onChange={(e) => setFilter("vendor", e.target.value)}
        >
          <option value="all">所有厂商</option>
          {[...new Set(project?.snapshot?.devices.map((d) => d.vendor) || [])]
            .filter(Boolean)
            .map((v) => (
              <option key={v}>{v}</option>
            ))}
        </select>
        <select
          aria-label="地址域筛选"
          value={filters.domain}
          onChange={(e) => setFilter("domain", e.target.value)}
        >
          <option value="all">所有地址域</option>
          {project?.domains.domains.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <div className="toolbar-spacer" />
        <button
          title="重新推断拓扑"
          aria-label="重新推断拓扑"
          disabled={!project?.snapshot || importing}
          onClick={() =>
            void mutate("/infer", {})
              .then(() => notify("推断已更新"))
              .catch(showError)
          }
        >
          <RefreshCw size={16} />
        </button>
        <button
          title="手工添加链路"
          onClick={() => openAction({ type: "force_link" })}
          disabled={!project?.snapshot}
        >
          <Link2 size={16} />
        </button>
        <div className="layout-control">
          <select
            aria-label="布局算法"
            value={layout}
            onChange={(e) => setLayout(e.target.value)}
          >
            <option value="components">分区网格 · 快速</option>
            <option value="fcose">fCoSE 力导向（≤150）</option>
            <option value="elk">ELK 分层</option>
            <option value="elk-radial">ELK 径向</option>
            <option value="elk-mrtree">ELK 多树</option>
            <option value="circle">环形</option>
            <option value="concentric">同心圆</option>
            <option value="grid">网格</option>
            <option value="breadthfirst">层次</option>
          </select>
          <button
            title="运行自动布局"
            onClick={() => graph.current?.layout(layout, onlySelected)}
          >
            <GitBranch size={16} />
            <span>自动布局</span>
          </button>
        </div>
        <label className="check-label selected-layout">
          <input
            type="checkbox"
            checked={onlySelected}
            onChange={(e) => setOnlySelected(e.target.checked)}
          />
          选中
        </label>
        <button
          onClick={() => openAction({ type: "view" })}
          disabled={!project}
        >
          <Save size={15} />
          <span>保存视图</span>
        </button>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          <AlertCircle size={17} />
          <span>{error}</span>
          <button
            onClick={() => {
              setError("");
              if (project) void loadProject(project.id);
            }}
          >
            刷新
          </button>
          <button aria-label="关闭错误" onClick={() => setError("")}>
            <X size={15} />
          </button>
        </div>
      )}
      {job && (
        <div className={"job-banner " + (importing ? "" : "done")}>
          <span className={importing ? "spinner" : ""} />
          <strong>{importing ? "导入配置" : statusName(job.status)}</strong>
          <progress
            max="100"
            value={job.progress <= 1 ? job.progress * 100 : job.progress}
          />
          <span>
            {Math.round(job.progress <= 1 ? job.progress * 100 : job.progress)}%
          </span>
          <button
            onClick={() => openAction({ type: "job", title: "导入结果" })}
          >
            查看结果
          </button>
          {importing ? (
            <button
              onClick={() =>
                void api<Job>(`/api/imports/${job.id}/cancel`, {
                  method: "POST",
                })
                  .then(setJob)
                  .catch(showError)
              }
            >
              取消导入
            </button>
          ) : (
            <button aria-label="关闭导入进度" onClick={() => setJob(null)}>
              <X size={14} />
            </button>
          )}
        </div>
      )}
      <nav className="mobile-nav">
        <button
          className={mobilePanel === "inventory" ? "active" : ""}
          onClick={() => setMobilePanel("inventory")}
        >
          <PanelLeft size={16} />
          设备
        </button>
        <button
          className={mobilePanel === "canvas" ? "active" : ""}
          onClick={() => setMobilePanel("canvas")}
        >
          <Network size={16} />
          拓扑
        </button>
        <button
          className={mobilePanel === "inspector" ? "active" : ""}
          onClick={() => setMobilePanel("inspector")}
        >
          <PanelRight size={16} />
          详情
        </button>
      </nav>
      <main className="workspace">
        {project ? (
          <Inventory
            devices={inventoryDevices}
            project={project}
            selection={selection}
            onSelect={(s) => {
              setSelection(s);
              setMobilePanel("inspector");
            }}
            search={filters.search}
            setSearch={(s) => setFilter("search", s)}
            onSource={(r) => void openSource(r)}
            onAction={(type) =>
              type === "domains" ? setDomainsOpen(true) : openAction({ type })
            }
          />
        ) : (
          <aside className="inventory welcome-sidebar">
            <h3>项目工作区</h3>
            <p>从设备配置开始，整理网络事实和可解释的逻辑关系。</p>
            <button onClick={() => openAction({ type: "project" })}>
              <Plus size={16} />
              新建项目
            </button>
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void demo()}
            >
              <Play size={15} />
              载入示例配置
            </button>
          </aside>
        )}
        <section className="canvas-section">
          <div className="canvas-heading">
            <div>
              <span className="live-dot" />
              IP 逻辑拓扑{" "}
              <small>
                {filtered.nodes.filter((n) => n.kind === "device").length}{" "}
                台设备 · {filtered.edges.length} 条关系
              </small>
            </div>
            <button
              className="text-button"
              disabled={
                !filters.focus &&
                (!selection || !["device", "segment"].includes(selection.kind))
              }
              onClick={() =>
                setFilter("focus", filters.focus ? "" : selection?.id || "")
              }
            >
              {filters.focus ? "显示全部" : "聚焦相邻"}
            </button>
            <button
              className="text-button view-settings-button"
              onClick={() => setViewSettings(!viewSettings)}
            >
              显示设置
            </button>
            <select
              aria-label="切换命名视图"
              value={view.id}
              onChange={(e) => {
                const selected = project?.views.find(
                  (v) => v.id === e.target.value,
                );
                if (selected)
                  void flushView()
                    .then(() => {
                      setView(selected);
                      viewRef.current = selected;
                      const f = { ...initialFilters, ...selected.filters };
                      setFilters(f);
                      if (f.mode !== filters.mode && project)
                        void loadProject(project.id, f.mode);
                    })
                    .catch(showError);
              }}
            >
              {activeViewOptions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </div>
          {viewSettings && (
            <div className="view-settings">
              <label>
                分组
                <select
                  aria-label="节点分组"
                  value={String(view.groups.mode || "none")}
                  onChange={(e) =>
                    viewChanged({ groups: { mode: e.target.value } })
                  }
                >
                  <option value="none">不分组</option>
                  <option value="vendor">按厂商</option>
                  <option value="site">按站点</option>
                </select>
              </label>
              {[
                ["names", "设备名称"],

                ["prefixes", "链路前缀"],
              ].map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={view.labels[key] !== false}
                    onChange={(e) =>
                      viewChanged({
                        labels: { ...view.labels, [key]: e.target.checked },
                      })
                    }
                  />
                  {label}
                </label>
              ))}
            </div>
          )}
          <Graph
            ref={graph}
            key={`${project?.id || "empty"}:${(project?.topology.nodes.length || 0) > 300 ? "large" : "normal"}`}
            data={filtered}
            focusId={filters.focus}
            largeGraph={(project?.topology.nodes.length || 0) > 300}
            view={view}
            selection={selection}
            onSelect={(s) => {
              setSelection(s);
              if (s && window.innerWidth < 760) setMobilePanel("inspector");
            }}
            onChange={viewChanged}
            onConnect={(a, b) =>
              openAction({
                type: "force_link",
                initial: { device_a_id: a, device_b_id: b },
              })
            }
            emptyAction={() => openAction({ type: "import" })}
            onError={setError}
          />
          {project && !project.snapshot && (
            <button
              className="demo-action"
              disabled={busy}
              onClick={() => void demo()}
            >
              <Play size={14} />
              载入四厂商示例
            </button>
          )}
        </section>
        {project ? (
          <Inspector
            project={project}
            selection={selection}
            onSelect={setSelection}
            onSource={(r) => void openSource(r)}
            onAction={openAction}
            onMutate={mutateSafe}
          />
        ) : (
          <aside className="inspector empty-inspector">
            <div className="panel-heading">证据检查器</div>
            <div className="empty-detail">
              <Network size={36} />
              <h3>让每条关系都有依据</h3>
              <p>选择拓扑对象，查看接口地址、来源配置与推断规则。</p>
            </div>
          </aside>
        )}
      </main>
      {project && (
        <Diagnostics
          project={project}
          onSource={(r) => void openSource(r)}
          onSelect={setSelection}
          onOverride={(id, status) =>
            void mutate(`/overrides/${id}`, { status }, "PATCH")
              .then(() => notify("修改记录已更新"))
              .catch(showError)
          }
          onHistory={(id) => void compareHistory(id)}
        />
      )}
      <footer className="statusbar">
        <span>
          {project?.snapshot?.devices.length || 0} 台设备 ·{" "}
          {project?.snapshot?.interfaces.length || 0} 个接口 ·{" "}
          {project?.snapshot?.links.filter((l) => l.review_state !== "rejected")
            .length || 0}{" "}
          条关系
        </span>
        <span>配置推断 ≠ 物理连接或实时可达</span>
        <a
          href="https://github.com/LiuMaoDou/IPtopo"
          target="_blank"
          rel="noreferrer"
        >
          IPtopo <span>↗</span>
        </a>
      </footer>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={16} />
          {toast}
        </div>
      )}
      {busy && (
        <div className="loading-indicator" role="status">
          <span className="spinner" />
          读取项目…
        </div>
      )}
      {action && action.type !== "job" && (
        <ActionDialog
          key={action.type + JSON.stringify(action.target)}
          action={action}
          project={project}
          dialects={capabilities.dialects}
          onSubmit={submit}
          onClose={() => setAction(null)}
        />
      )}
      {action?.type === "job" && job && (
        <Modal title="导入结果" wide onClose={() => setAction(null)}>
          <div className="job-results">
            <p>状态：{statusName(job.status)}</p>
            {job.results?.map((r, i) => (
              <div className="result-row" key={i}>
                <strong>
                  {String(r.filename || r.name || "文件 " + (i + 1))}
                </strong>
                <span>
                  {statusName(String(r.status || r.parse_status || ""))}
                </span>
                <pre>{JSON.stringify(r, null, 2)}</pre>
              </div>
            ))}
            {job.errors?.map((e, i) => (
              <pre className="error-box" key={i}>
                {typeof e === "string" ? e : JSON.stringify(e, null, 2)}
              </pre>
            ))}
          </div>
        </Modal>
      )}
      {domainsOpen && project && (
        <DomainDialog
          project={project}
          onSave={async (domains) => {
            await mutate("/domains", { domains }, "PUT");
            notify("地址域已更新");
          }}
          onClose={() => setDomainsOpen(false)}
        />
      )}
      {source && (
        <SourceDialog
          source={source}
          line={source.line}
          endLine={source.endLine}
          onClose={() => setSource(null)}
        />
      )}
      {history && (
        <Modal title={history.title} onClose={() => setHistory(null)}>
          <p className="form-note">
            以选定历史快照为基准，统计当前解析事实的增删改。
          </p>
          <table>
            <thead>
              <tr>
                <th>对象</th>
                <th>新增</th>
                <th>删除</th>
                <th>变更</th>
              </tr>
            </thead>
            <tbody>
              {history.items.map((i) => (
                <tr key={i.kind}>
                  <td>{i.kind}</td>
                  <td>{i.added}</td>
                  <td>{i.removed}</td>
                  <td>{i.changed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}
    </div>
  );
}
