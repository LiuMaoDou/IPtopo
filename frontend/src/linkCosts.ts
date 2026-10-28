import type { GraphData, Snapshot } from "./types";
import type { RoutingInstance } from "./routingTypes";

type Cost = { protocol: RoutingInstance["protocol"]; scope: string; value: number | null };
type InterfaceCost = { deviceId: string; costs: Cost[] };
export type LinkCostLabel = { center: string; source: string; target: string };

/** Reuse effective routing facts, including confirmed bandwidth assumptions. */
export function indexInterfaceCosts(snapshot?: Snapshot | null) {
  const result = new Map<string, InterfaceCost>();
  const interfaces = new Map(snapshot?.interfaces.map(iface => [iface.id, iface]));
  const instances = new Map(snapshot?.routing?.instances.map(instance => [instance.id, instance]));
  for (const binding of snapshot?.routing?.bindings || []) {
    const iface = interfaces.get(binding.interface_id);
    const instance = instances.get(binding.instance_id);
    if (!iface || !instance || iface.device_id !== binding.device_id || instance.device_id !== binding.device_id
      || instance.routing_context_id !== iface.routing_context_id) continue;
    const entry = result.get(iface.id) || { deviceId: iface.device_id, costs: [] };
    entry.costs.push({ protocol: instance.protocol, scope: binding.scope,
      value: typeof binding.cost === "number" && Number.isFinite(binding.cost) ? binding.cost : null });
    result.set(iface.id, entry);
  }
  return result;
}

function summarizeCosts(costs: Cost[]) {
  const values = [...new Set(costs.map(cost => cost.value))];
  const known = values.filter((value): value is number => value !== null).sort((a, b) => a - b);
  const parts = known.length > 3 ? [`${known[0]}–${known[known.length - 1]}`] : known.map(String);
  if (values.includes(null)) parts.push("待确认");
  return parts.join(" / ");
}

function formatCosts(costs: Cost[], showProtocol: boolean) {
  const protocols = [...new Set(costs.map(cost => cost.protocol))].sort();
  return protocols.flatMap(protocol => {
    const values = costs.filter(cost => cost.protocol === protocol);
    const scopes = [...new Set(values.map(cost => cost.scope))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    const byScope = scopes.map(scope => ({ scope, summary: summarizeCosts(values.filter(cost => cost.scope === scope)) }));
    const name = protocol === "isis" ? "IS-IS" : "OSPF";
    // Distinguish scopes when their metrics differ; equal values need one label.
    if (new Set(byScope.map(item => item.summary)).size > 1)
      return byScope.map(({ scope, summary }) => `${name} ${protocol === "isis" ? `L${scope}` : `Area ${scope}`} Cost ${summary}`);
    return [`${showProtocol || protocols.length > 1 ? `${name} ` : ""}Cost ${summarizeCosts(values)}`];
  }).join("\n");
}

/** Align each bundle member to the displayed edge before combining directions. */
export function linkCostLabels(edges: GraphData["edges"], bundles: ReadonlyMap<string, GraphData["edges"]>, costs: ReadonlyMap<string, InterfaceCost>) {
  const result = new Map<string, LinkCostLabel>();
  const atEnd = (edge: GraphData["edges"][number], side: "source" | "target") => {
    const ids = side === "source" ? [edge.interface_a_id, edge.interface_b_id] : [edge.interface_b_id, edge.interface_a_id];
    return ids.map(id => costs.get(id)).find(entry => entry?.deviceId === edge[side])?.costs || [];
  };
  for (const edge of edges) {
    const members = edge.member_ids ? bundles.get(edge.bundle_id || edge.id) || [] : [edge];
    const sourceCosts: Cost[] = [], targetCosts: Cost[] = [];
    for (const member of members) {
      // RoutingFacts currently describe IPv4 IGP metrics only.
      if (member.families?.length && !member.families.includes(4)) continue;
      const forward = member.source === edge.source;
      sourceCosts.push(...atEnd(member, forward ? "source" : "target"));
      targetCosts.push(...atEnd(member, forward ? "target" : "source"));
    }
    const showProtocol = new Set([...sourceCosts, ...targetCosts].map(cost => cost.protocol)).size > 1;
    const source = formatCosts(sourceCosts, showProtocol), target = formatCosts(targetCosts, showProtocol);
    if (source || target) result.set(edge.id, source === target
      ? { center: source, source: "", target: "" }
      : { center: "", source, target });
  }
  return result;
}
