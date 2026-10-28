export type PanelMode = "inspector" | "simulation" | "lsdb" | "chat";
export type PanelSide = "left" | "right";
export type PanelPreferences = {
  version: 1;
  left?: number;
  right: Partial<Record<PanelMode, number>>;
  collapsed?: Partial<Record<PanelSide, boolean>>;
};

export const PANEL_WIDTHS_KEY = "iptopo.panel-widths.v1";
const modes: PanelMode[] = ["inspector", "simulation", "lsdb", "chat"];
const validWidth = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

export function parsePanelPreferences(raw: string | null): PanelPreferences {
  const result: PanelPreferences = { version: 1, right: {} };
  try {
    const data = JSON.parse(raw || "null");
    if (data?.version !== 1) return result;
    if (validWidth(data.left)) result.left = data.left;
    for (const mode of modes)
      if (validWidth(data.right?.[mode])) result.right[mode] = data.right[mode];
    for (const side of ["left", "right"] as const)
      if (typeof data.collapsed?.[side] === "boolean")
        result.collapsed = { ...result.collapsed, [side]: data.collapsed[side] };
  } catch { /* Ignore unavailable or invalid saved preferences. */ }
  return result;
}

export function panelWidths(width: number, mode: PanelMode, prefs: PanelPreferences) {
  const wide = width >= 1600;
  const leftDefault = mode === "inspector" ? (wide ? 270 : width <= 1250 ? 220 : 250) : wide ? 250 : 220;
  const rightDefault = mode === "chat" ? (wide ? 420 : 390)
    : mode === "inspector" ? (wide ? 320 : width <= 1250 ? 270 : 300) : wide ? 380 : 360;
  const leftMin = 180, rightMin = mode === "inspector" ? 260 : 320;
  const leftCollapsed = prefs.collapsed?.left === true;
  const rightCollapsed = prefs.collapsed?.right === true;
  // Preserve usable space for the topology when restoring wide saved sidebars.
  const budget = Math.max((leftCollapsed ? 0 : leftMin) + (rightCollapsed ? 0 : rightMin), width - 360);
  const left = leftCollapsed ? 0 : Math.round(clamp(prefs.left ?? leftDefault, leftMin, Math.min(480, budget - (rightCollapsed ? 0 : rightMin))));
  const right = rightCollapsed ? 0 : Math.round(clamp(prefs.right[mode] ?? rightDefault, rightMin, Math.min(720, budget - left)));
  return {
    left, right, leftMin, rightMin,
    leftMax: Math.min(480, budget - right),
    rightMax: Math.min(720, budget - left),
  };
}

export function setPanelCollapsed(prefs: PanelPreferences, side: PanelSide, collapsed: boolean): PanelPreferences {
  return { ...prefs, collapsed: { ...prefs.collapsed, [side]: collapsed } };
}

export function setPanelWidth(prefs: PanelPreferences, side: PanelSide, mode: PanelMode, width?: number): PanelPreferences {
  return side === "left" ? { ...prefs, left: width }
    : { ...prefs, right: { ...prefs.right, [mode]: width } };
}
