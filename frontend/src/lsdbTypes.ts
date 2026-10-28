import type { GraphData, Ref } from "./types";
import type { SimulationResult } from "./routingTypes";
export type LSDBBatch = {
  vendor?: "huawei" | "cisco" | "juniper" | "nokia"; adapter?: string;
  id: string; name: string; protocol: "ospf" | "isis"; process_id: string; scope: string;
  context_name: string; domain: string; collector: string; collected_at: string | null;
  imported_at: string; declared_complete: boolean; record_count: number;
  diagnostic_count: number; blocking_count: number;
};
export type LSDBNode = {
  id: string; protocol_id: string; label: string; kind: string; observed?: boolean; mapping_status: string;
  instance_id: string | null; device_id: string | null; source_refs: Ref[];
};
export type LSDBComparison = {
  id: string; source: string; target: string; status: string;
  config_cost: number | null; observed_cost: number | null;
  interface_name?: string; source_refs: Ref[]; config_source_refs: Ref[];
};
export type LSDBAnalysis = {
  batch_id: string; project_version: number; mappings: Record<string, string>;
  topology: GraphData; nodes: LSDBNode[];
  destinations: { id: string; node_id: string; prefix: string; cost: number; source_refs: Ref[] }[];
  comparisons: LSDBComparison[];
  diagnostics: { code: string; message: string; blocking: boolean; source_refs?: Ref[] }[];
  simulation: (SimulationResult & { hops: (SimulationResult["hops"][number] & { interface_name?: string })[] }) | null;
  config_simulation: SimulationResult | null;
};
export const lsdbStatus = (s: string) => ({ consistent: "一致", cost_difference: "开销差异", state_difference: "状态差异", config_only: "仅配置", lsdb_only: "仅 LSDB", port_ambiguous: "端口待关联", config_cost_unknown: "配置开销未知", matched: "已关联", ambiguous: "身份冲突" }[s] || s);
