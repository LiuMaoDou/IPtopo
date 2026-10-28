import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import cytoscape, {
  type Core,
  type Layouts,
  type NodeSingular,
  type EdgeSingular,
} from "cytoscape";
import edgehandles from "cytoscape-edgehandles";
import undoRedo from "cytoscape-undo-redo";
import {
  Plus,
  Minus,
  LocateFixed,
  Undo2,
  Redo2,
  Pin,
  Link2,
  MousePointer2,
  Focus,
  SlidersHorizontal,
  Network,
  X,
} from "lucide-react";
import type { GraphData, View, Selection, Snapshot } from "./types";
import { parallelLinks, placeMissingNodes } from "./graphModel";
import { indexInterfaceCosts, linkCostLabels } from "./linkCosts";
import { canvasEndpointLabel, linkEndpointLabels } from "./linkEndpoints";
import { segmentIcon, vendorStyle, vendors } from "./vendorIcons";
import type { SimulationHighlight } from "./routingTypes";
import { simulationHopsByEdge, type SimulationEndpointRole, type SimulationEndpoints } from "./simulationModel";
cytoscape.use(edgehandles);
cytoscape.use(undoRedo);
const endpointOffset = (edge: EdgeSingular) => {
  const a = edge.source().position(), b = edge.target().position();
  return Math.max(25, Math.hypot(a.x - b.x, a.y - b.y) * 0.18);
};
export type GraphHandle = {
  layout: (name: string, selected?: boolean) => void;
  png: () => void;
  fit: () => void;
  reveal: (ids: string[]) => void;
  capture: () => Partial<View>;
  pin: () => void;
};
type Props = {
  data: GraphData;
  snapshot?: Snapshot | null;
  readOnly?: boolean;
  simulation?: SimulationHighlight | null;
  simulationEndpoints?: SimulationEndpoints | null;
  emphasis?: { deviceIds: string[]; linkIds: string[] } | null;
  focusId?: string;
  onToggleFocus?: () => void;
  largeGraph?: boolean;
  view: View;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onChange: (v: Partial<View>) => void;
  onConnect: (a: string, b: string) => void;
  emptyAction: () => void;
  onError: (s: string) => void;
  onSimulationEndpoint?: (role: SimulationEndpointRole, deviceId: string) => void;
};
export default forwardRef<GraphHandle, Props>(function Graph(
  {
    data,
    snapshot,
    readOnly = false,
    simulation = null,
    simulationEndpoints = null,
    emphasis = null,
    focusId,
    onToggleFocus,
    largeGraph = false,
    view,
    selection,
    onSelect,
    onChange,
    onConnect,
    emptyAction,
    onError,
    onSimulationEndpoint,
  },
  ref,
) {
  const container = useRef<HTMLDivElement>(null),
    cyRef = useRef<Core | null>(null),
    callbacks = useRef({ onSelect, onChange, onConnect, onError, onSimulationEndpoint }),
    viewRef = useRef(view),
    worker = useRef<Worker | null>(null),
    layoutRef = useRef<Layouts | null>(null),
    undo = useRef<any>(null),
    edges = useRef<any>(null),
    suppress = useRef(false),
    timer = useRef<ReturnType<typeof setTimeout> | null>(null),
    layoutName = useRef("preset"),
    runId = useRef(0),
    rendered = useRef(
      new Map<string, { source: unknown; presentation: string }>(),
    ),
    lodKey = useRef(""),
    zoomFrame = useRef<number | null>(null),
    initialized = useRef(false),
    previousFocus = useRef(focusId),
    restoredView = useRef(view.id),
    skipViewportSave = useRef(false);
  const [zoom, setZoom] = useState(100),
    [busy, setBusy] = useState(false),
    [drawing, setDrawing] = useState(false);
  const [expandedBundles, setExpandedBundles] = useState<Set<string>>(() => new Set());
  const [contextMenu, setContextMenu] = useState<{ deviceId: string; label: string; x: number; y: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const display = useMemo(() => {
    if (readOnly) return { ...data, bundles: new Map<string, GraphData["edges"]>() };
    return parallelLinks(data, expandedBundles);
  }, [data, expandedBundles, readOnly]);
  const interfaceCosts = useMemo(() => indexInterfaceCosts(readOnly ? null : snapshot), [snapshot, readOnly]);
  const costLabels = useMemo(() => linkCostLabels(display.edges, display.bundles, interfaceCosts), [display, interfaceCosts]);
  const interfaces = useMemo(() => new Map(snapshot?.interfaces.map(iface => [iface.id, iface])), [snapshot]);
  const endpointLabels = useMemo(() => linkEndpointLabels(display.edges, interfaces), [display, interfaces]);
  const bundleRef = useRef(display.bundles);
  bundleRef.current = display.bundles;
  callbacks.current = { onSelect, onChange, onConnect, onError, onSimulationEndpoint };
  viewRef.current = view;
  useEffect(() => {
    if (!contextMenu) return;
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const dismiss = (event: PointerEvent) => { if (!menuRef.current?.contains(event.target as Node)) setContextMenu(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setContextMenu(null); };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [contextMenu]);
  const canChooseEndpoint = !!onSimulationEndpoint;
  useEffect(() => {
    if (!settingsOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (!settingsRef.current?.contains(event.target as Node)) setSettingsOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [settingsOpen]);
  useEffect(() => { setContextMenu(null); }, [data, canChooseEndpoint]);
  const capture = (): Partial<View> => {
    const cy = cyRef.current;
    if (!cy) return {};
    const positions: View["positions"] = { ...viewRef.current.positions };
    cy.nodes().forEach((n) => {
      positions[n.id()] = { ...n.position() };
    });
    return {
      positions,
      pinned: [
        ...new Set([
          ...viewRef.current.pinned.filter((id) => !cy.$id(id).length),
          ...cy.nodes(":locked").map((n) => n.id()),
        ]),
      ],
      zoom: cy.zoom(),
      pan: cy.pan(),
      layout: layoutName.current,
    };
  };
  const save = () => {
    if (suppress.current || skipViewportSave.current) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(
      () => callbacks.current.onChange(capture()),
      700,
    );
  };
  const positions = () => {
    const result: View["positions"] = {};
    cyRef.current?.nodes().forEach((n) => {
      result[n.id()] = { ...n.position() };
    });
    return result;
  };
  const updateLOD = (force = false) => {
    const cy = cyRef.current;
    if (!cy) return;
    const large = cy.nodes().length > 150;
    const tier =
      cy.zoom() < 0.28 ? "overview" : cy.zoom() < 0.65 ? "compact" : "detail";
    const key = large + ":" + tier;
    if (!force && lodKey.current === key) return;
    lodKey.current = key;
    cy.batch(() => {
      cy.elements().toggleClass("large-graph", large);
      cy.elements().toggleClass("lod-overview", tier === "overview");
      cy.elements().toggleClass("lod-compact", tier !== "detail");
    });
  };
  const stop = () => {
    runId.current++;
    worker.current?.terminate();
    worker.current = null;
    layoutRef.current?.stop();
    layoutRef.current = null;
    setBusy(false);
  };
  const runLayout = async (name: string, selected = false) => {
    const cy = cyRef.current;
    if (!cy || !cy.nodes().length) return;
    if (selected && !cy.nodes(":selected").length) {
      callbacks.current.onError("请先选择节点，再运行选中节点布局。");
      return;
    }
    const targetCount = selected
      ? cy.nodes(":selected").length
      : cy.nodes().length;
    if (name === "fcose" && targetCount > 150) {
      callbacks.current.onError(
        "fCoSE 适用于 150 个以内节点。大拓扑请使用分区网格或可取消的 ELK 布局，也可先聚焦相邻节点。",
      );
      return;
    }
    stop();
    const currentRun = runId.current;
    setBusy(true);
    const before = positions();
    const nodes =
      selected && cy.nodes(":selected").length
        ? cy.nodes(":selected")
        : cy.nodes().filter((n) => n.data("kind") !== "group");
    const movable = nodes.filter((n) => !n.locked());
    const done = () => {
      if (runId.current !== currentRun) return;
      setBusy(false);
      const after = positions();
      undo.current?.do("positions", { before, after, alreadyApplied: true });
      save();
    };
    layoutName.current = name;
    try {
      if (name === "components") {
        const ids = new Set(nodes.map((n) => n.id()));
        const graphData = {
          nodes: data.nodes.filter((n) => ids.has(n.id)),
          edges: data.edges.filter(
            (e) => ids.has(e.source) && ids.has(e.target),
          ),
        };
        const fixed: View["positions"] = {};
        nodes.forEach((n) => {
          if (n.locked()) fixed[n.id()] = { ...n.position() };
        });
        const result = placeMissingNodes(graphData, fixed);
        cy.batch(() =>
          nodes.forEach((n) => {
            if (!n.locked() && result[n.id()]) n.position(result[n.id()]);
          }),
        );
        if (!selected) cy.fit(nodes, 55);
        done();
        return;
      }
      if (name.startsWith("elk")) {
        const w = new Worker(new URL("./layout.worker.ts", import.meta.url), {
          type: "module",
        });
        worker.current = w;
        let request: unknown;
        const ids = new Set(nodes.map((n) => n.id()));
        w.onmessage = ({ data: result }) => {
          if (worker.current !== w) return;
          if (result.id === 0 && !result.error) {
            w.postMessage({
              id: 1,
              cmd: "layout",
              graph: request,
              layoutOptions: {},
              options: {},
            });
            return;
          }
          w.terminate();
          worker.current = null;
          if (result.error) {
            setBusy(false);
            callbacks.current.onError(
              typeof result.error === "string"
                ? result.error
                : result.error.message || JSON.stringify(result.error),
            );
            return;
          }
          const origin = nodes.boundingBox();
          suppress.current = true;
          cy.batch(() => {
            for (const n of result.data.children || []) {
              const node = cy.$id(n.id);
              if (!node.locked())
                node.position({
                  x: n.x + origin.x1 + 70,
                  y: n.y + origin.y1 + 30,
                });
            }
          });
          suppress.current = false;
          if (!selected) cy.fit(undefined, 70);
          done();
        };
        w.onerror = (e) => {
          stop();
          callbacks.current.onError(e.message);
        };
        w.postMessage({
          id: 0,
          cmd: "register",
          algorithms: ["layered", "radial", "mrtree"],
        });
        request = {
          id: "root",
          layoutOptions: {
            "elk.algorithm":
              name === "elk-radial"
                ? "radial"
                : name === "elk-mrtree"
                  ? "mrtree"
                  : "layered",
            "elk.direction": "DOWN",
            "elk.spacing.nodeNode": "85",
            "elk.layered.spacing.nodeNodeBetweenLayers": "130",
          },
          children: nodes.map((n) => ({ id: n.id(), width: 130, height: 90 })),
          edges: cy
            .edges()
            .filter(
              (e: any) => ids.has(e.source().id()) && ids.has(e.target().id()),
            )
            .map((e: any) => ({
              id: e.id(),
              sources: [e.source().id()],
              targets: [e.target().id()],
            })),
        };
        return;
      }
      if (name === "fcose") {
        const module = await import("cytoscape-fcose");
        cytoscape.use(module.default);
        if (runId.current !== currentRun) return;
      }
      const eles =
        name === "fcose"
          ? nodes.union(nodes.edgesWith(nodes))
          : movable.union(movable.edgesWith(movable));
      const opts: any = {
        name,
        eles,
        animate: false,
        fit: !selected,
        padding: 70,
        nodeDimensionsIncludeLabels: true,
        avoidOverlap: true,
        nodeRepulsion: () => 9000,
        idealEdgeLength: () => 120,
        quality: "default",
        randomize: true,
        stop: done,
      };
      if (name === "fcose")
        opts.fixedNodeConstraint = nodes
          .filter((n) => n.locked())
          .map((n: any) => ({ nodeId: n.id(), position: { ...n.position() } }));
      layoutRef.current = eles.layout(opts);
      layoutRef.current.run();
    } catch (e) {
      setBusy(false);
      callbacks.current.onError(String(e));
    }
  };
  useImperativeHandle(ref, () => ({
    layout: runLayout,
    fit: () => cyRef.current?.fit(undefined, 70),
    reveal: (ids) => {
      const cy = cyRef.current;
      if (!cy) return;
      let targets = cy.collection();
      for (const id of ids) targets = targets.union(cy.getElementById(id));
      if (targets.length) { cy.fit(targets, 70); cy.zoom(Math.min(cy.zoom(), 1.3)); cy.center(targets); }
    },
    capture,
    pin: () => {
      const ns = cyRef.current?.nodes(":selected");
      ns?.forEach((n) => {
        n.locked() ? n.unlock() : n.lock();
      });
      callbacks.current.onChange(capture());
    },
    png: () => {
      const cy = cyRef.current;
      if (!cy) return;
      const result = cy.png({
        bg: "#f8fafb",
        full: true,
        maxWidth: 4096,
        maxHeight: 4096,
        output: "blob-promise",
      }) as unknown as Promise<Blob>;
      void result
        .then((blob) => {
          const url = URL.createObjectURL(blob),
            a = document.createElement("a");
          a.href = url;
          a.download = "IPtopo.png";
          a.click();
          setTimeout(() => URL.revokeObjectURL(url), 10000);
        })
        .catch((error) => callbacks.current.onError(String(error)));
    },
  }));
  useEffect(() => {
    const cy = cytoscape({
      container: container.current!,
      elements: [],
      minZoom: 0.015,
      pixelRatio: largeGraph ? 1 : "auto",
      hideEdgesOnViewport: largeGraph,
      textureOnViewport: largeGraph,
      maxZoom: 3,
      boxSelectionEnabled: true,
      style: [
        {
          selector: "node",
          style: {
            shape: "roundrectangle",
            width: 56,
            height: 54,
            "background-color": "#fff",
            "background-opacity": 0,
            "border-width": 0,
            label: "data(displayLabel)",
            "text-wrap": "wrap",
            "text-valign": "bottom",
            "text-halign": "center",
            "text-margin-y": 8,
            "font-family": "system-ui, sans-serif",
            "font-size": 12,
            "min-zoomed-font-size": 8,
            color: "#172b3a",
            "background-image": "data(icon)",
            "background-width": 44,
            "background-height": 44,
            "background-position-x": "50%",
            "background-position-y": "50%",
            "text-max-width": "155px",
          },
        },
        {
          selector: 'node[kind="device"]',
          style: {
            "background-color": "#f5fafb",
            "background-opacity": 1,
            "border-color": "data(vendorColor)",
            "border-width": 2,
          },
        },
        {
          selector: 'node[kind="segment"]',
          style: {
            shape: "roundrectangle",
            width: 44,
            height: 44,
            label: "data(displayLabel)",
            "background-color": "#e4f4f5",
            "border-color": "#087f80",
            "border-width": 1,
            "background-width": 32,
            "background-height": 32,
            "font-size": 16,
            "text-max-width": "280px",
            "text-overflow-wrap": "anywhere",
            "text-margin-x": 0,
            "text-margin-y": 10,
            "text-valign": "bottom",
            "background-opacity": 1,
          },
        },
        {
          selector: 'node[kind="group"]',
          style: {
            shape: "roundrectangle",
            "background-color": "#edf4f5",
            "background-opacity": 0.35,
            "border-color": "#cddde2",
            "border-style": "dashed",
            "border-width": 1,
            "background-image": "none",
            "text-valign": "top",
            "text-margin-y": -8,
            "text-margin-x": 0,
            padding: "24px",
            "font-size": 12,
            color: "#62848e",
          },
        },
        {
          selector: 'node:selected[kind != "device"]',
          style: {
            "border-color": "#087f80",
            "border-width": 3,
            "background-color": "#effafa",
            "background-opacity": 1,
          },
        },
        {
          selector: "node:locked",
          style: { "border-style": "double", "border-width": 4 },
        },
        {
          selector: "edge",
          style: {
            width: 1.8,
            "line-color": "#7d929e",
            "line-style": "dashed",
            "curve-style": "bezier",
            "control-point-step-size": 55,
            label: "data(displayLabel)",
            "font-family": "system-ui, sans-serif",
            "font-size": 11,
            "min-zoomed-font-size": 8,
            color: "#526a78",
            "text-wrap": "wrap",
            "text-rotation": "autorotate",
            "text-background-color": "#f8fafb",
            "text-background-opacity": 0.95,
            "text-background-padding": "4px",
            "text-margin-y": -9,
          },
        },
        {
          selector: 'edge[review_state="confirmed"]',
          style: { "line-color": "#087f80", "line-style": "solid", width: 2.4 },
        },
        {
          selector: 'edge[origin="manual"]',
          style: { "line-color": "#5260a3", "line-style": "solid" },
        },
        {
          selector: "edge.large-graph",
          style: {
            label: "",
            "curve-style": "haystack",
            "haystack-radius": 0,
            "line-style": "solid",
            width: 1.2,
          },
        },
        { selector: "edge.lod-compact", style: { label: "" } },
        {
          selector: 'node.lod-overview[kind="device"]',
          style: {
            label: "",
            "background-image": "none",
            "background-color": "data(vendorColor)",
            "background-opacity": 1,
            shape: "roundrectangle",
            "border-width": 1,
            width: 30,
            height: 30,
          },
        },
        {
          selector: 'node.lod-overview[kind="segment"]',
          style: {
            label: "",
            width: 28,
            height: 28,
            "background-width": 20,
            "background-height": 20,
          },
        },
        {
          selector: "node:selected",
          style: {
            label: "data(displayLabel)",
            "background-image": "data(icon)",
            "min-zoomed-font-size": 0,
          },
        },
        {
          selector: 'node[kind="segment"]:selected',
          style: {
            width: 50,
            height: 50,
            "background-width": 36,
            "background-height": 36,
            "text-background-color": "#fff",
            "text-background-opacity": 0.95,
            "text-background-padding": "5px",
          },
        },
        {
          selector: 'node.lod-overview[kind="segment"]:selected',
          style: {
            width: 32,
            height: 32,
            "background-width": 24,
            "background-height": 24,
          },
        },
        {
          selector: 'node[kind="device"]:selected',
          style: {
            "background-color": "data(vendorSelectedBackground)",
            width: 64,
            height: 62,
            "background-width": 50,
            "background-height": 50,
          },
        },
        {
          selector: 'node.lod-overview[kind="device"]:selected',
          style: {
            width: 34,
            height: 34,
            "background-image": "none",
          },
        },
        {
          selector: "edge:selected",
          style: {
            "line-color": "#04a2a4",
            "curve-style": "bezier",
            label: "data(displayLabel)",
            "min-zoomed-font-size": 0,
            width: 3,
            color: "#087f80",
            "source-label": "data(source_label)",
            "target-label": "data(target_label)",
            "source-text-offset": 35,
            "target-text-offset": 35,
            "source-text-margin-y": 14,
            "target-text-margin-y": 14,
            "source-text-rotation": "autorotate",
            "target-text-rotation": "autorotate",
          },
        },
        {
          selector: "edge[bundle_id]",
          style: { "curve-style": "bezier", "control-point-step-size": 60 },
        },
        {
          selector: "edge[expanded_bundle = 1]",
          style: {
            "control-point-step-size": edge => edge.data("parallel_spacing"),
            label: "data(displayLabel)",
            "font-size": 13,
            "min-zoomed-font-size": 0,
            "text-background-color": "#fff",
            "text-background-opacity": 1,
            "text-background-padding": "5px",
            "text-background-shape": "roundrectangle",
            "text-border-color": "#d5e2e7",
            "text-border-width": 1,
            "text-border-opacity": 1,
            "text-margin-y": 0,
            color: "#24424f",
          },
        },
        { selector: "edge.lod-compact[expanded_bundle = 1]", style: { "font-size": 16 } },
        { selector: "edge.lod-overview[expanded_bundle = 1]", style: { label: "" } },
        {
          selector: "edge[link_count]",
          style: {
            width: "data(bundle_width)",
            label: "data(displayLabel)",
            "font-size": 13,
            "font-weight": "bold",
            "text-background-opacity": 1,
            "text-margin-y": -12,
            "min-zoomed-font-size": 7,
          },
        },
        {
          selector: "edge[link_count]:selected",
          style: { "min-zoomed-font-size": 0 },
        },
        {
          selector: "edge[cost_visible = 1]",
          style: {
            label: "data(displayLabel)",
            "source-label": "data(source_cost_label)", "target-label": "data(target_cost_label)",
            "source-text-offset": 65, "target-text-offset": 65,
            "source-text-margin-y": -12, "target-text-margin-y": -12,
            "source-text-rotation": "autorotate", "target-text-rotation": "autorotate",
            "min-zoomed-font-size": 0, "font-size": 13,
          },
        },
        { selector: "edge.lod-compact[cost_visible = 1]", style: { "font-size": 16 } },
        { selector: "edge.large-graph[cost_visible = 1], edge.lod-overview[cost_visible = 1]", style: {
          label: "", "source-label": "", "target-label": "",
        } },
        { selector: "edge[cost_visible = 1]:selected", style: {
          label: "data(displayLabel)", "source-label": "data(source_detail_label)", "target-label": "data(target_detail_label)",
          "min-zoomed-font-size": 0,
        } },
        {
          selector: "edge[endpoint_visible = 1], edge[has_endpoints = 1]:selected",
          style: {
            label: "data(endpoint_center_label)",
            "source-label": "data(source_detail_label)", "target-label": "data(target_detail_label)",
            "source-text-offset": (edge: EdgeSingular) => edge.data("endpoint_offset"), "target-text-offset": (edge: EdgeSingular) => edge.data("endpoint_offset"),
            "source-text-margin-y": 0, "target-text-margin-y": 0,
            "source-text-rotation": "autorotate", "target-text-rotation": "autorotate",
            "text-max-width": "200px", "text-overflow-wrap": "anywhere",
            "text-background-color": "#fff", "text-background-opacity": 1,
            "text-background-padding": "4px", "text-margin-y": 0,
            "min-zoomed-font-size": 0,
          },
        },
        { selector: "edge.lod-overview[endpoint_visible = 1]:unselected, edge.large-graph[endpoint_visible = 1]:unselected", style: {
          label: "", "source-label": "", "target-label": "",
        } },
        { selector: "edge[has_endpoints = 1][expanded_bundle = 0]:selected", style: {
          "source-text-margin-y": -60, "target-text-margin-y": 60,
        } },
        {
          selector: ".eh-preview, .eh-ghost-edge",
          style: { "line-color": "#087f80", "target-arrow-color": "#087f80" },
        },
        { selector: 'edge[origin="observed"]', style: { "target-arrow-shape": "triangle", "line-color": "#427e9b", "target-arrow-color": "#427e9b" } },
        { selector: 'edge[status="cost_difference"], edge[status="state_difference"]', style: { "line-color": "#c77722", "target-arrow-color": "#c77722" } },
        { selector: 'edge[status="config_only"]', style: { "line-style": "dashed", "line-color": "#9ba6ad", "target-arrow-shape": "triangle", "target-arrow-color": "#9ba6ad" } },
        { selector: 'node[mapping_status="config_only"]', style: { "border-style": "dashed", "opacity": 0.65 } },
        { selector: ".simulation-muted", style: { opacity: 0.22 } },
        { selector: "node.simulation-endpoint", style: { "border-width": 4, "border-color": "#087f80", "background-color": "#d6f4ed" } },
        { selector: "node.simulation-source, node.simulation-destination", style: {
          "border-width": 4, label: "data(simulationEndpointLabel)", "min-zoomed-font-size": 0,
          "text-background-color": "#fff", "text-background-opacity": 0.95, "text-background-padding": "3px",
        } },
        { selector: "node.simulation-source", style: { "border-color": "#087f80", "background-color": "#d6f4ed" } },
        { selector: "node.simulation-destination", style: { "border-color": "#bb642b", "background-color": "#fff0dd" } },
        { selector: "node.simulation-source.simulation-destination", style: { "border-color": "#7960a8", "background-color": "#eee7fa" } },
        { selector: "edge.simulation-route", style: {
          "line-color": "#087f80", "target-arrow-color": "#087f80", "source-arrow-color": "#087f80",
          width: 4, label: "data(simulationLabel)", "font-size": 12, "min-zoomed-font-size": 0,
          "text-background-color": "#fff", "text-background-opacity": 1, "text-background-padding": "3px",
          "source-label": "", "target-label": "", "z-index": 20,
        } },
        { selector: "edge.simulation-forward", style: { "target-arrow-shape": "triangle" } },
        { selector: "edge.simulation-reverse", style: { "source-arrow-shape": "triangle" } },
        { selector: "edge.simulation-route[endpoint_visible = 1], edge.simulation-route[has_endpoints = 1]:selected", style: {
          "source-label": "data(source_endpoint_label)", "target-label": "data(target_endpoint_label)",
        } },
        { selector: ".chat-emphasis-muted", style: { opacity: 0.24 } },
        { selector: "node.chat-entity-focus", style: { "border-color": "#087f80", "border-width": 3, "background-color": "#e2f5f0", "text-opacity": 1 } },
        { selector: "edge.chat-entity-focus", style: { "line-color": "#087f80", "width": 4, "target-arrow-color": "#087f80" } },
      ],
    });
    cyRef.current = cy;
    undo.current = (cy as any).undoRedo({
      undoableDrag: true,
      stackSizeLimit: 20,
    });
    const apply = ({ before, after, alreadyApplied }: any) => {
      if (!alreadyApplied) {
        cy.batch(() =>
          cy.nodes().forEach((n) => {
            if (after[n.id()]) {
              const locked = n.locked();
              n.unlock();
              n.position(after[n.id()]);
              if (locked) n.lock();
            }
          }),
        );
        callbacks.current.onChange({
          ...capture(),
          positions: { ...viewRef.current.positions, ...after },
        });
      }
      return { before: after, after: before };
    };
    undo.current.action("positions", apply, apply);
    edges.current = (cy as any).edgehandles({
      canConnect: (a: NodeSingular, b: NodeSingular) =>
        !readOnly && a.id() !== b.id() &&
        a.data("kind") === "device" &&
        b.data("kind") === "device",
    });
    cy.on("ehcomplete", (_e, a, b, added) => {
      added.remove();
      edges.current.disableDrawMode();
      setDrawing(false);
      callbacks.current.onConnect(a.id(), b.id());
    });
    cy.on("tap", "node", (e) => {
      if (e.target.data("kind") !== "group")
        callbacks.current.onSelect({
          kind: e.target.data("kind") === "segment" ? "segment" : "device",
          id: e.target.id(),
        });
    });
    cy.on("cxttap", "node", (event) => {
      if (event.target.data("kind") !== "device" || !callbacks.current.onSimulationEndpoint) return;
      event.originalEvent?.preventDefault();
      edges.current?.disableDrawMode(); setDrawing(false);
      const position = event.renderedPosition || event.target.renderedPosition();
      const width = container.current?.clientWidth || 0;
      const height = container.current?.clientHeight || 0;
      callbacks.current.onSelect({ kind: "device", id: event.target.id() });
      setContextMenu({ deviceId: event.target.id(), label: String(event.target.data("label") || event.target.id()),
        x: Math.max(8, Math.min(position.x, width - 240)), y: Math.max(8, Math.min(position.y, height - 144)) });
    });
    cy.on("tapstart pan zoom", () => setContextMenu(null));
    cy.on("onetap", "edge", (e) => {
      if (e.target.data("link_count")) {
        callbacks.current.onSelect({
          kind: "bundle", id: e.target.id(), linkIds: e.target.data("member_link_ids"),
        });
      } else if (!e.target.hasClass("eh-preview"))
        callbacks.current.onSelect({
          kind: "link",
          id: e.target.data("link_id") || e.target.id(),
        });
    });
    cy.on("dbltap", "edge", (e) => {
      const id = e.target.data("bundle_id") as string | undefined;
      if (!id) return;
      const members = bundleRef.current.get(id);
      if (!members) return;
      setExpandedBundles((previous) => {
        const next = new Set(previous);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      callbacks.current.onSelect({
        kind: "bundle", id, linkIds: members.map((member) => member.link_id || member.id),
      });
    });
    cy.on("tap", (e) => {
      if (e.target === cy) callbacks.current.onSelect(null);
    });
    cy.on("dragfree", save);
    cy.on("position", "node", event => {
      event.target.connectedEdges().forEach((edge: EdgeSingular) => edge.data("endpoint_offset", endpointOffset(edge)));
    });
    cy.on("pan", save);
    cy.on("zoom", () => {
      if (zoomFrame.current !== null) return;
      zoomFrame.current = requestAnimationFrame(() => {
        zoomFrame.current = null;
        setZoom(Math.round(cy.zoom() * 100));
        updateLOD();
        save();
      });
    });
    const resize = new ResizeObserver(() => {
      cy.resize();
    });
    resize.observe(container.current!);
    return () => {
      stop();
      if (timer.current) clearTimeout(timer.current);
      if (zoomFrame.current !== null) cancelAnimationFrame(zoomFrame.current);
      resize.disconnect();
      edges.current?.destroy();
      cy.destroy();
      cyRef.current = null;
    };
  }, []);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    stop();
    suppress.current = true;
    const first = !initialized.current;
    const grouping = String(view.groups.mode || "none");
    const presentation =
      grouping +
      ":" +
      String(view.labels.names) +
      ":" +
      String(view.labels.prefixes) + ":" + String(view.labels.costs);
    const groups = new Map<
      string,
      { id: string; label: string; kind: string }
    >();
    const nodes = data.nodes.map((n) => {
      if (n.kind !== "device" || grouping === "none") return n;
      const name = String(n[grouping] || "未分类"),
        parent = "group:" + name;
      groups.set(parent, { id: parent, label: name, kind: "group" });
      return { ...n, parent };
    });
    const allNodes: GraphData["nodes"] = [...groups.values(), ...nodes];
    const existing = { ...view.positions };
    cy.nodes().forEach((n) => {
      if (n.data("kind") !== "group") existing[n.id()] = { ...n.position() };
    });
    const planned = placeMissingNodes(data, existing);
    const ids = new Set([...allNodes, ...display.edges].map((n) => n.id));
    const sources = new Map(data.nodes.map((n) => [n.id, n]));
    cy.batch(() => {
      cy.elements()
        .filter((e) => !ids.has(e.id()))
        .remove();
      for (const id of rendered.current.keys())
        if (!ids.has(id)) rendered.current.delete(id);
      for (const n of allNodes) {
        const node = cy.$id(n.id),
          source = sources.get(n.id) || n,
          previous = rendered.current.get(n.id);
        if (
          node.length &&
          previous?.source === source &&
          previous.presentation === presentation
        )
          continue;
        const vendor = vendorStyle(n.vendor);
        const d = {
          id: n.id,
          label: n.label,
          kind: n.kind,
          mapping_status: n.mapping_status,
          parent: n.parent ? String(n.parent) : undefined,
          icon:
            n.kind === "device"
              ? vendor.icon
              : n.kind === "segment"
                ? segmentIcon
                : "none",
          vendorColor: vendor.color,
          vendorSelectedBackground: vendor.selectedBackground,
          displayLabel:
            n.kind === "group"
              ? n.label
              : view.labels.names === false
                ? ""
                : n.mapping_status === "config_only" ? `${n.label}（仅配置）` : n.label,
        };
        if (node.length) {
          node.data(d);
          if (n.kind === "device" && node.parent()[0]?.id() !== n.parent)
            node.move({ parent: String(n.parent || "") || null });
        } else
          cy.add({
            group: "nodes",
            data: d,
            position: planned[n.id] || view.positions[n.id],
          });
        if (view.pinned.includes(n.id)) cy.$id(n.id).lock();
        rendered.current.set(n.id, { source, presentation });
      }
      const edgeLabels = new Map(display.edges.map(e => {
        const costLabel = view.labels.costs !== false ? costLabels.get(e.id) : undefined;
        const expanded = !!e.bundle_id && !e.link_count;
        const rawEndpoint = endpointLabels.get(e.id)!;
        const endpoint = { source: canvasEndpointLabel(rawEndpoint.source), target: canvasEndpointLabel(rawEndpoint.target) };
        const hasEndpoints = !!(endpoint.source || endpoint.target);
        const showEndpoints = view.labels.prefixes !== false && expanded && hasEndpoints;
        const baseLabel = e.link_count ? e.label : view.labels.prefixes === false ? "" : e.prefixes?.map((prefix: string) => `网段 ${prefix}`).join("\n") || e.label || "";
        const unavailable = expanded && view.labels.costs !== false && !costLabel && e.families?.length && !e.families.includes(4)
          ? "IPv6 · Cost 未解析" : "";
        const center = costLabel?.center || unavailable;
        return [e.id, { costLabel, expanded, endpoint, hasEndpoints, showEndpoints, center,
          displayLabel: [showEndpoints ? "" : baseLabel, center].filter(Boolean).join("\n"),
          sourceDetail: [endpoint.source || e.source_label, costLabel?.source].filter(Boolean).join("\n"),
          targetDetail: [endpoint.target || e.target_label, costLabel?.target].filter(Boolean).join("\n"),
        }];
      }));
      // A quadratic curve's midpoint moves half its control-point distance.
      // Give every member the same spacing, sized for the tallest label in its bundle.
      const bundleSpacing = new Map<string, number>();
      for (const e of display.edges) {
        const labels = edgeLabels.get(e.id)!;
        if (!labels.expanded) continue;
        const rows = Math.max(...[labels.displayLabel, labels.sourceDetail, labels.targetDetail]
          .map(text => text.split("\n").reduce((total, line) => total + Math.max(1, Math.ceil(line.length / 22)), 0)));
        bundleSpacing.set(e.bundle_id!, Math.max(bundleSpacing.get(e.bundle_id!) || 144, (rows * 18 + 20) * 2));
      }
      for (const e of display.edges) {
        if (!cy.$id(e.source).length || !cy.$id(e.target).length) continue;
        const edge = cy.$id(e.id),
          previous = rendered.current.get(e.id);
        const labels = edgeLabels.get(e.id)!;
        const costLabel = labels.costLabel;
        const parallelSpacing = bundleSpacing.get(e.bundle_id || "") || 60;
        const edgePresentation = `${presentation}:${labels.displayLabel}:${labels.sourceDetail}:${labels.targetDetail}:${parallelSpacing}:${labels.showEndpoints}`;
        if (
          edge.length &&
          previous?.source === e &&
          previous.presentation === edgePresentation
        )
          continue;
        const d = {
          id: e.id,
          source: e.source,
          target: e.target,
          link_id: e.link_id,
          origin: e.origin,
          status: e.status,
          cost: e.cost,
          review_state: e.review_state,
          bundle_id: e.bundle_id,
          expanded_bundle: labels.expanded ? 1 : 0,
          has_endpoints: labels.hasEndpoints ? 1 : 0,
          endpoint_visible: labels.showEndpoints ? 1 : 0,
          endpoint_center_label: labels.center,
          source_endpoint_label: labels.endpoint.source,
          target_endpoint_label: labels.endpoint.target,
          endpoint_offset: 25,
          parallel_spacing: parallelSpacing,
          member_link_ids: e.member_link_ids,
          link_count: e.link_count,
          bundle_width: e.bundle_width,
          source_label: e.source_label || "",
          target_label: e.target_label || "",
          cost_visible: costLabel ? 1 : 0,
          source_cost_label: costLabel?.source || "",
          target_cost_label: costLabel?.target || "",
          source_detail_label: labels.sourceDetail,
          target_detail_label: labels.targetDetail,
          displayLabel: labels.displayLabel,
        };
        if (edge.length) edge.data(d);
        else cy.add({ group: "edges", data: d });
        const added = cy.$id(e.id) as EdgeSingular;
        added.data("endpoint_offset", endpointOffset(added));
        rendered.current.set(e.id, { source: e, presentation: edgePresentation });
      }
    });
    suppress.current = false;
    updateLOD(true);
    if (first && data.nodes.length) {
      initialized.current = true;
      if (!Object.keys(view.positions).length) {
        if (data.nodes.length <= 150) void runLayout("fcose");
        else {
          layoutName.current = "components";
          cy.fit(undefined, 55);
          callbacks.current.onChange(capture());
        }
      }
    }
  }, [data, display, costLabels, endpointLabels, view.labels, view.groups]);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("simulation-muted simulation-route simulation-forward simulation-reverse simulation-endpoint");
      if (!simulation) return;
      cy.elements().addClass("simulation-muted");
      for (const id of [simulation.source_device_id, simulation.destination_device_id]) {
        cy.getElementById(id).removeClass("simulation-muted").addClass("simulation-endpoint");
      }
      const byEdge = simulationHopsByEdge(display.edges, simulation);
      cy.edges().forEach(edge => {
        const hops = byEdge.get(edge.id());
        if (!hops) return;
        const isForward = (hop: typeof hops[number]) => edge.source().id() === hop.source_device_id || edge.target().id() === hop.target_device_id;
        const costs = [...new Set(hops.map(hop => hop.cost))].sort((a, b) => a - b).join(" / ");
        const costPrefix = view.labels.costs !== false ? `Cost ${edge.data("origin") === "observed" ? edge.data("cost") : costs} · ` : "";
        edge.data("simulationLabel", `${costPrefix}${Number(hops.reduce((total, h) => total + h.share_percent, 0).toFixed(2))}%`);
        edge.removeClass("simulation-muted").addClass("simulation-route");
        if (hops.some(isForward)) edge.addClass("simulation-forward");
        if (hops.some(hop => !isForward(hop))) edge.addClass("simulation-reverse");
        edge.connectedNodes().removeClass("simulation-muted");
      });
    });
  }, [simulation, data, display, view.labels.costs]);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().removeClass("simulation-source simulation-destination");
      if (!simulationEndpoints) return;
      const { sourceDeviceId, destinationDeviceId } = simulationEndpoints;
      for (const id of new Set([sourceDeviceId, destinationDeviceId].filter(Boolean))) {
        const node = cy.getElementById(id);
        if (!node.length) continue;
        const role = id === sourceDeviceId && id === destinationDeviceId ? "源 / 目的" : id === sourceDeviceId ? "源" : "目的";
        node.data("simulationEndpointLabel", `${role} · ${node.data("displayLabel") || node.data("label") || id}`);
        node.removeClass("simulation-muted");
        if (id === sourceDeviceId) node.addClass("simulation-source");
        if (id === destinationDeviceId) node.addClass("simulation-destination");
      }
    });
  }, [simulationEndpoints, simulation, data, display]);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("chat-emphasis-muted chat-entity-focus");
    if (!emphasis) return;
    const linkIds = new Set(emphasis.linkIds);
    let targets = cy.collection();
    for (const id of emphasis.deviceIds) targets = targets.union(cy.getElementById(id));
    cy.edges().forEach(edge => {
      const members: string[] = edge.data("member_link_ids") || [];
      if (linkIds.has(edge.id()) || linkIds.has(edge.data("link_id")) || members.some(id => linkIds.has(id))) targets = targets.union(edge).union(edge.connectedNodes());
    });
    if (!targets.length) return;
    cy.batch(() => {
      if (!simulation) cy.elements().addClass("chat-emphasis-muted");
      targets.removeClass("chat-emphasis-muted");
      if (!simulation) targets.addClass("chat-entity-focus");
    });
    skipViewportSave.current = true;
    cy.fit(targets, 70); cy.zoom(Math.min(cy.zoom(), 1.3)); cy.center(targets);
    requestAnimationFrame(() => { skipViewportSave.current = false; });
  }, [emphasis, simulation, data, display]);
  useEffect(() => {
    if (previousFocus.current === focusId) return;
    previousFocus.current = focusId;
    const cy = cyRef.current;
    if (cy?.nodes().length) {
      cy.fit(undefined, 70);
      if (focusId) cy.zoom(Math.min(cy.zoom(), 1.25));
      cy.center();
    }
  }, [focusId]);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (restoredView.current !== view.id) stop();
    restoredView.current = view.id;
    suppress.current = true;
    cy.nodes().forEach((n) => {
      n.unlock();
      if (view.positions[n.id()]) n.position(view.positions[n.id()]);
      view.pinned.includes(n.id()) ? n.lock() : n.unlock();
    });
    if (Object.keys(view.positions).length) {
      cy.zoom(view.zoom);
      cy.pan(view.pan);
    }
    suppress.current = false;
  }, [view.id]);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selection) {
      if (selection.kind === "link") {
        for (const [id, members] of display.bundles) {
          if (!expandedBundles.has(id) && members.some((member) => (member.link_id || member.id) === selection.id)) {
            setExpandedBundles((previous) => new Set(previous).add(id));
            return;
          }
        }
      }
      cy.$id(selection.id).select();
      cy.edges()
        .filter((e) => e.data("link_id") === selection.id)
        .select();
    }
  }, [selection, display]);
  return (
    <div className="graph-wrap" onContextMenu={event => { if (onSimulationEndpoint) event.preventDefault(); }}>
      <div ref={container} className="graph" aria-label="可交互 IP 拓扑画布" />
      {contextMenu && onSimulationEndpoint && <div className="graph-context-menu" ref={menuRef} role="menu" aria-label={`${contextMenu.label} 仿真端点`} style={{ left: contextMenu.x, top: contextMenu.y }}>
        <strong>{contextMenu.label}</strong>
        <button role="menuitem" onClick={() => { onSimulationEndpoint("source", contextMenu.deviceId); setContextMenu(null); }}>设为仿真源</button>
        <button role="menuitem" onClick={() => { onSimulationEndpoint("destination", contextMenu.deviceId); setContextMenu(null); }}>设为仿真目的</button>
      </div>}
      {!data.nodes.length && (
        <div className="canvas-empty">
          <Network size={46} />
          <h2>{readOnly ? "从 LSDB，还原协议拓扑" : "从配置，还原网络"}</h2>
          <p>{readOnly ? "在右侧导入或选择一个采集批次。" : "导入设备配置，查看可追溯的 IP 逻辑关系。"}</p>
          {!readOnly && <button className="primary" onClick={emptyAction}>导入配置</button>}
        </div>
      )}
      <div className="graph-topbar">
        <div className="graph-actions">
          <button
            title="选择 / 拖动"
            aria-label="选择模式"
            className={!drawing ? "active" : ""}
            onClick={() => {
              edges.current?.disableDrawMode();
              setDrawing(false);
            }}
          >
            <MousePointer2 size={17} />
          </button>
          <button
            title="拖动连接设备，随后选择接口"
            aria-label="手工连线模式"
            disabled={readOnly}
            className={drawing ? "active" : ""}
            onClick={() => {
              edges.current?.enableDrawMode();
              setDrawing(true);
            }}
          >
            <Link2 size={17} />
          </button>
          <span />
          <button
            title={focusId ? "显示全部" : "聚焦相邻"}
            aria-label={focusId ? "显示全部" : "聚焦相邻"}
            aria-pressed={!!focusId}
            className={focusId ? "active" : ""}
            disabled={!onToggleFocus || !focusId && (!selection || !["device", "segment"].includes(selection.kind))}
            onClick={onToggleFocus}
          >
            <Focus size={17} />
          </button>
          <button
            title="固定 / 释放选中节点"
            aria-label="固定选中节点"
            onClick={() => {
              cyRef.current?.nodes(":selected").forEach((n) => {
                n.locked() ? n.unlock() : n.lock();
              });
              callbacks.current.onChange(capture());
            }}
          >
            <Pin size={16} />
          </button>
          <button
            title="撤销移动或布局"
            aria-label="撤销布局"
            onClick={() => {
              undo.current?.undo();
              save();
            }}
          >
            <Undo2 size={17} />
          </button>
          <button
            title="重做移动或布局"
            aria-label="重做布局"
            onClick={() => {
              undo.current?.redo();
              save();
            }}
          >
            <Redo2 size={17} />
          </button>
          <span />
          <div className="graph-settings" ref={settingsRef} onKeyDown={event => {
            if (event.key === "Escape") {
              event.preventDefault();
              setSettingsOpen(false);
              settingsRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
            }
          }}>
            <button title="显示设置" aria-label="显示设置" aria-expanded={settingsOpen}
              className={settingsOpen ? "active" : ""} disabled={readOnly}
              onClick={() => setSettingsOpen(open => !open)}>
              <SlidersHorizontal size={17} />
            </button>
            {settingsOpen && !readOnly && <div className="view-settings" role="group" aria-label="拓扑显示设置">
              <label>分组
                <select aria-label="节点分组" value={String(view.groups.mode || "none")}
                  onChange={event => onChange({ groups: { mode: event.target.value } })}>
                  <option value="none">不分组</option>
                  <option value="vendor">按厂商</option>
                  <option value="site">按站点</option>
                </select>
              </label>
              {[["names", "设备名称"], ["prefixes", "链路地址"], ["costs", "链路 Cost"]].map(([key, label]) =>
                <label key={key}><input type="checkbox" checked={view.labels[key] !== false}
                  onChange={event => onChange({ labels: { ...view.labels, [key]: event.target.checked } })} />{label}</label>)}
            </div>}
          </div>
        </div>
        {simulationEndpoints && (simulationEndpoints.sourceDeviceId || simulationEndpoints.destinationDeviceId) && <div className="simulation-endpoint-legend" role="status" aria-label="已选仿真端点">
          {simulationEndpoints.sourceDeviceId && <span className="source"><i />源：{data.nodes.find(node => node.id === simulationEndpoints.sourceDeviceId)?.label || simulationEndpoints.sourceDeviceId}</span>}
          {simulationEndpoints.destinationDeviceId && <span className="destination"><i />目的：{data.nodes.find(node => node.id === simulationEndpoints.destinationDeviceId)?.label || simulationEndpoints.destinationDeviceId}</span>}
        </div>}
      </div>
      <div className="zoom-controls">
        <button
          aria-label="放大"
          onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)}
        >
          <Plus size={17} />
        </button>
        <button
          aria-label="缩小"
          onClick={() => cyRef.current?.zoom(cyRef.current.zoom() / 1.2)}
        >
          <Minus size={17} />
        </button>
        <span>{zoom}%</span>
        <button
          title="将整个拓扑居中并调整缩放"
          aria-label="回到中心"
          className="recenter-button"
          disabled={!data.nodes.length}
          onClick={() => cyRef.current?.fit(undefined, 70)}
        >
          <LocateFixed size={17} />回到中心
        </button>
      </div>
      <div className="vendor-legend" aria-label="节点图标图例">
        {vendors
          .filter((v) =>
            data.nodes.some(
              (n) => n.kind === "device" && vendorStyle(n.vendor).key === v.key,
            ),
          )
          .map((v) => (
            <span key={v.key}>
              <img src={v.icon} alt="" />
              {v.name}
            </span>
          ))}
        {data.nodes.some((n) => n.kind === "segment") && (
          <span>
            <img src={segmentIcon} alt="" />
            {readOnly ? "广播网段 / 伪节点" : "IP 网段"}
          </span>
        )}
      </div>
      <div className="graph-legend">
        {display.bundles.size > 0 && <span>粗线：多链路 · 双击展开 / 收起</span>}
        <span>
          <i />
          {readOnly ? "仅配置" : "推断关系"}
        </span>
        <span>
          <i className="confirmed" />
          {readOnly ? "观测 / 最短路径" : "人工确认"}
        </span>
        <span>
          <i className={readOnly ? "lsdb-difference" : "manual"} />
          {readOnly ? "配置差异" : "手工连接"}
        </span>
      </div>
      {busy && (
        <div className="layout-progress">
          <span className="spinner" />
          正在计算布局
          <button onClick={stop}>
            <X size={14} />
            取消
          </button>
        </div>
      )}
    </div>
  );
});
