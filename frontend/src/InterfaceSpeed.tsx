import { useState } from "react";
import type { Interface } from "./types";
import type { RoutingBinding } from "./routingTypes";
import { parseInterfaceRate } from "./interfaceRate";

export default function InterfaceSpeed({ iface, bindings, disabled, onSave }: {
  iface: Interface;
  bindings: RoutingBinding[];
  disabled: boolean;
  onSave: (mbps: number | null) => Promise<void>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const inferred = bindings.find(binding => binding.bandwidth_mbps)?.bandwidth_mbps;
  const current = iface.assumed_bandwidth_mbps ?? iface.configured_bandwidth_mbps ?? inferred;
  const costs = [...new Set(bindings.filter(binding => binding.cost !== null).map(binding => binding.cost))];
  const commit = async () => {
    if (draft === null || saving || disabled) return;
    try {
      const mbps = parseInterfaceRate(draft);
      if (mbps === (iface.assumed_bandwidth_mbps ?? null)) { setDraft(null); return; }
      setSaving(true); setError("");
      await onSave(mbps);
      setDraft(null); setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };
  return <div className="interface-speed">
    <label htmlFor={`speed-${iface.id}`}>仿真速率</label>
    <div className="interface-speed-input">
      <input id={`speed-${iface.id}`} aria-label="仿真速率（Gbit/s）" aria-invalid={!!error}
        type="number" min="0" step="any" max="1000000" placeholder="自动识别 / 待确认"
        value={draft ?? (current ? String(current / 1000) : "")} disabled={disabled || saving}
        onChange={event => { setDraft(event.target.value); setSaved(false); setError(""); }}
        onBlur={() => void commit()} onKeyDown={event => {
          if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); }
          if (event.key === "Escape") { setDraft(null); setError(""); }
        }} />
      <span>Gbit/s</span>
    </div>
    {error ? <p role="alert" className="interface-speed-error">{error}</p> :
      <small role="status">{saving ? "正在保存…" : saved ? "已保存，开销已重算" :
        iface.assumed_bandwidth_mbps ? "人工设置 · 配置推演假设" : current ? "自动推演 · 可直接修改" : "带宽待确认 · 可手动填写"}</small>}
    <small>回车或移开焦点保存；留空恢复自动。显式配置开销优先。{costs.length ? `当前协议开销：${costs.join(" / ")}。` : ""}</small>
  </div>;
}
