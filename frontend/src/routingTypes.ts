import type { Ref } from "./types";

export type RoutingInstance = {
  id: string; device_id: string; routing_context_id: string;
  protocol: "ospf" | "isis"; local_name: string; router_id: string | null;
  areas: string[]; levels: string[]; max_paths: number | null;
};
export type RoutingBinding = {
  id: string; device_id: string; instance_id: string; interface_id: string;
  scope: string; passive: boolean; network_type: string;
  cost: number | null; cost_origin: string; metric_limit: number; source_refs: Ref[];
};
export type RoutingAdvertisement = {
  id: string; device_id: string; binding_id: string; interface_id: string;
  address_id: string; address: string; prefix: string;
};
export type RoutingFacts = {
  schema_version: 1; instances: RoutingInstance[]; bindings: RoutingBinding[];
  advertisements: RoutingAdvertisement[];
  issues: { code: string; message: string; device_id: string; source_refs: Ref[] }[];
};
export type SimulationHop = {
  id: string; source_device_id: string; target_device_id: string;
  interface_id: string; peer_interface_id: string; binding_id: string;
  interface_name?: string; next_hop: string; cost: number; link_ids: string[];
  share_percent: number; local_share_percent: number; source_refs: Ref[];
};
export type SimulationHighlight = {
  source_device_id: string; destination_device_id: string; hops: SimulationHop[];
};
export type SimulationResult = SimulationHighlight & {
  status: "ok" | "unreachable" | "incomplete" | "unsupported";
  snapshot_id: string; project_version: number; total_cost: number | null;
  path_count: string; paths_truncated: boolean; forwarding_status: string;
  paths: { hops: string[]; device_ids: string[]; total_cost: number; share_percent: number }[];
  assumptions: string[];
  diagnostics: { code: string; message: string; source_refs?: Ref[]; interface_id?: string; ignored?: boolean; blocking?: boolean }[];
};
