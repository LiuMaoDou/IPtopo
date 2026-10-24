export type Ref = {
  source_file_id: string;
  line: number;
  end_line?: number;
  text?: string;
  parser?: string;
};
export type Device = {
  id: string;
  hostname: string;
  vendor: string;
  os: string;
  format: string;
  site?: string;
  source_file_id?: string;
  source_refs: Ref[];
  notes?: unknown[];
};
export type Address = {
  id: string;
  family: number;
  address: string | null;
  prefix_length: number | null;
  canonical_network: string | null;
  role: string;
  acquisition_method: string;
  effective_state: string;
  source_refs: Ref[];
};
export type Interface = {
  id: string;
  device_id: string;
  routing_context_id: string;
  raw_name: string;
  normalized_name: string;
  kind: string;
  description: string;
  admin_state: string;
  oper_state: string;
  effective_state: string;
  addresses: Address[];
  source_refs: Ref[];
  aggregate_members?: string[];
  aggregate_interface_id?: string;
  aggregation?: {
    protocol?: string;
    mode?: string;
    periodic?: string;
    minimum_links?: number;
    link_speed?: string;
    source_refs: Ref[];
  };
  physical_port_refs?: string[];
  parent_interface_id?: string;
};
export type Link = {
  id: string;
  interface_a_id: string;
  interface_b_id: string;
  device_a_id: string;
  device_b_id: string;
  relation_type: string;
  origin: string;
  review_state: string;
  families: number[];
  prefixes: string[];
  segment_ids: string[];
  evidence: Evidence[];
  diagnostics: unknown[];
};
export type Evidence = {
  rule: string;
  domain_id: string;
  family: number;
  prefix: string;
  interface_ids: string[];
  source_refs: Ref[];
};
export type Segment = {
  id: string;
  domain_id: string;
  family: number;
  prefix: string;
  interface_ids: string[];
  evidence: Evidence[];
  status: string;
};
export type Diagnostic = {
  id: string;
  severity: string;
  code: string;
  message: string;
  source_file_id?: string;
  line?: number;
  device_id?: string;
  interface_id?: string;
};
export type SourceFile = {
  id: string;
  filename: string;
  parse_status: string;
  vendor: string;
  os: string;
  format: string;
  interface_count: number;
  ipv4_count: number;
  ipv6_count: number;
  diagnostics: Diagnostic[];
};
export type Snapshot = {
  id: string;
  devices: Device[];
  interfaces: Interface[];
  contexts: {
    id: string;
    device_id: string;
    local_name: string;
    kind: string;
    rd?: string;
    rt?: string[];
  }[];
  source_files: SourceFile[];
  diagnostics: Diagnostic[];
  segments: Segment[];
  links: Link[];
  unmatched: unknown[];
};
export type Domains = {
  domains: { id: string; name: string; confirmed: boolean }[];
  mappings: Record<string, string>;
  interface_mappings: Record<string, string>;
  segment_splits: Record<string, unknown>;
};
export type GraphData = {
  nodes: {
    id: string;
    label: string;
    kind: string;
    vendor?: string;
    [key: string]: unknown;
  }[];
  edges: (Link & {
    source: string;
    target: string;
    label: string;
    link_id?: string;
    source_label?: string;
    target_label?: string;
    bundle_id?: string;
    member_ids?: string[];
    member_link_ids?: string[];
    link_count?: number;
    bundle_width?: number;
  })[];
};
export type View = {
  id: string;
  name: string;
  positions: Record<string, { x: number; y: number }>;
  pinned: string[];
  filters: Record<string, string>;
  groups: Record<string, unknown>;
  labels: Record<string, unknown>;
  layout: string;
  zoom: number;
  pan: { x: number; y: number };
};
export type Override = {
  id: string;
  operation: string;
  target: Record<string, unknown>;
  value: Record<string, unknown>;
  reason: string;
  status: string;
  created_at: string;
  conflict_reason?: string;
  conflict?: string;
};
export type Job = {
  id: string;
  status: string;
  progress: number;
  results: Record<string, unknown>[];
  errors: unknown[];
  total?: number;
  processed?: number;
};
export type Project = {
  id: string;
  name: string;
  description: string;
  version: number;
  snapshot_id: string;
  snapshot: Snapshot | null;
  topology: GraphData;
  domains: Domains;
  overrides: Override[];
  views: View[];
  jobs: Job[];
  snapshots: { id: string; created_at: string; device_count?: number }[];
};
export type Summary = { id: string; name: string; device_count: number };
export type Selection = {
  kind: "device" | "segment" | "link" | "interface" | "bundle";
  id: string;
  linkIds?: string[];
} | null;
export type Mutation = {
  operation: string;
  target: Record<string, unknown>;
  value: Record<string, unknown>;
  reason?: string;
  baseline?: Record<string, unknown>;
};
export async function api<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const r = await fetch(url, {
    ...options,
    headers:
      options.body instanceof FormData
        ? options.headers
        : { "Content-Type": "application/json", ...options.headers },
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(
      typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail),
    );
  }
  return r.json();
}
export const blankView = (id = "default", name = "默认视图"): View => ({
  id,
  name,
  positions: {},
  pinned: [],
  filters: {},
  groups: {},
  labels: {},
  layout: "preset",
  zoom: 1,
  pan: { x: 0, y: 0 },
});
export const statusName = (s: string) =>
  ({
    pending: "待审阅",
    confirmed: "已确认",
    rejected: "已拒绝",
    success: "成功",
    partial: "部分成功",
    failed: "失败",
    active: "有效",
    inactive: "未启用",
    unresolved: "待解析",
    enabled: "已配置启用",
    disabled: "已关闭",
    unknown: "未知",
    completed: "已完成",
    cancelled: "已取消",
    running: "处理中",
    queued: "排队中",
    conflict: "冲突",
    undone: "已撤销",
    manual: "人工",
    inferred: "推断",
    observed: "观测",
  })[s] || s;
