import { useLayoutEffect, useRef, useState, type ReactNode, type KeyboardEvent, type PointerEvent } from "react";
import { PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen } from "lucide-react";
import { PANEL_WIDTHS_KEY, panelWidths, parsePanelPreferences, setPanelWidth, setPanelCollapsed, type PanelMode, type PanelPreferences, type PanelSide } from "./panelWidths";

function readPreferences() {
  try { return parsePanelPreferences(localStorage.getItem(PANEL_WIDTHS_KEY)); }
  catch { return parsePanelPreferences(null); }
}
function savePreferences(prefs: PanelPreferences) {
  try { localStorage.setItem(PANEL_WIDTHS_KEY, JSON.stringify(prefs)); }
  catch { /* Resizing still works when browser storage is disabled. */ }
}

export default function ResizableWorkspace({ mode, children }: { mode: PanelMode; children: ReactNode }) {
  const root = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(() => Math.max(1280, window.innerWidth));
  const [preferences, setPreferences] = useState(readPreferences);
  const preferencesRef = useRef(preferences);
  const drag = useRef<{ side: PanelSide; mode: PanelMode; pointerId: number; x: number; width: number; before: PanelPreferences } | null>(null);
  const [resizing, setResizing] = useState<PanelSide | null>(null);
  const sizes = panelWidths(width, mode, preferences);
  const previousMode = useRef(mode);

  const togglePanel = (side: PanelSide, collapsed = !preferencesRef.current.collapsed?.[side]) => {
    const next = setPanelCollapsed(preferencesRef.current, side, collapsed);
    preferencesRef.current = next;
    setPreferences(next);
    savePreferences(next);
  };
  useLayoutEffect(() => {
    if (previousMode.current === mode) return;
    previousMode.current = mode;
    if (preferencesRef.current.collapsed?.right) togglePanel("right", false);
  }, [mode]);

  useLayoutEffect(() => {
    const element = root.current!;
    const measure = () => setWidth(element.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const update = (side: PanelSide, value: number | undefined, save = false) => {
    const min = side === "left" ? sizes.leftMin : sizes.rightMin;
    const max = side === "left" ? sizes.leftMax : sizes.rightMax;
    const next = setPanelWidth(preferencesRef.current, side, mode,
      value === undefined ? undefined : Math.round(Math.min(max, Math.max(min, value))));
    preferencesRef.current = next;
    setPreferences(next);
    if (save) savePreferences(next);
  };
  const start = (event: PointerEvent<HTMLDivElement>, side: PanelSide) => {
    if (event.button !== 0 || drag.current) return;
    event.preventDefault();
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { side, mode, pointerId: event.pointerId, x: event.clientX, width: sizes[side], before: preferencesRef.current };
    setResizing(side);
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId || active.mode !== mode) return;
    update(active.side, active.width + (event.clientX - active.x) * (active.side === "left" ? 1 : -1));
  };
  const finish = (event: PointerEvent<HTMLDivElement>, cancel = false) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    if (cancel) {
      preferencesRef.current = active.before;
      setPreferences(active.before);
    } else savePreferences(preferencesRef.current);
    setResizing(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const keyDown = (event: KeyboardEvent<HTMLDivElement>, side: PanelSide) => {
    if (drag.current) return;
    const step = event.shiftKey ? 40 : 10;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const direction = (event.key === "ArrowRight" ? 1 : -1) * (side === "left" ? 1 : -1);
      update(side, sizes[side] + direction * step, true);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      update(side, side === "left" ? (event.key === "Home" ? sizes.leftMin : sizes.leftMax)
        : event.key === "Home" ? sizes.rightMin : sizes.rightMax, true);
    } else if (event.key === "Enter") {
      event.preventDefault();
      update(side, undefined, true);
    }
  };

  return <main ref={root} className="workspace" data-resizing={resizing || undefined}
    data-left-collapsed={preferences.collapsed?.left || undefined}
    data-right-collapsed={preferences.collapsed?.right || undefined}
    style={{ gridTemplateColumns: `${sizes.left}px minmax(0, 1fr) ${sizes.right}px` }}>
    {children}
    {(["left", "right"] as const).filter(side => !preferences.collapsed?.[side]).map(side => <div key={side}
      className={`panel-resizer panel-resizer-${side}`}
      style={side === "left" ? { left: sizes.left - 4 } : { right: sizes.right - 4 }}
      role="separator" tabIndex={0} aria-orientation="vertical"
      aria-label={side === "left" ? "调整左侧栏宽度" : "调整右侧栏宽度"}
      aria-valuemin={side === "left" ? sizes.leftMin : sizes.rightMin}
      aria-valuemax={side === "left" ? sizes.leftMax : sizes.rightMax}
      aria-valuenow={sizes[side]} aria-valuetext={`${sizes[side]} 像素`}
      title="拖动调整宽度 · 双击恢复默认 · 方向键微调"
      onPointerDown={event => start(event, side)} onPointerMove={move}
      onPointerUp={event => finish(event)} onPointerCancel={event => finish(event, true)}
      onLostPointerCapture={event => finish(event)}
      onKeyDown={event => keyDown(event, side)} onDoubleClick={() => update(side, undefined, true)}
    />)}
    {(["left", "right"] as const).map(side => {
      const collapsed = !!preferences.collapsed?.[side];
      const label = `${collapsed ? "展开" : "收起"}${side === "left" ? "左" : "右"}侧栏`;
      const Icon = side === "left" ? (collapsed ? PanelLeftOpen : PanelLeftClose) : collapsed ? PanelRightOpen : PanelRightClose;
      return <button key={`toggle-${side}`} className="panel-collapse" title={label} aria-label={label}
        aria-expanded={!collapsed} style={side === "left" ? { left: sizes.left + 8 } : { right: sizes.right + 8 }}
        onClick={() => togglePanel(side)}><Icon size={16} /></button>;
    })}
  </main>;
}
