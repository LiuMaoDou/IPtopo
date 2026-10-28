import type { GraphData, Interface, Project } from "./types";
import type { RoutingInstance, SimulationHighlight, SimulationHop } from "./routingTypes";

export type SimulationEndpointRole = "source" | "destination";
export type SimulationEndpoints = {
  protocol: RoutingInstance["protocol"] | "";
  sourceDeviceId: string;
  sourceInterfaceId: string;
  destinationDeviceId: string;
  destinationInterfaceId: string;
};

export const emptySimulationEndpoints = (): SimulationEndpoints => ({
  protocol: "", sourceDeviceId: "", sourceInterfaceId: "",
  destinationDeviceId: "", destinationInterfaceId: "",
});

export function simulationContextKey(project: Project | null): string {
  return JSON.stringify([project?.id, project?.snapshot_id,
    project?.effective_revision || [project?.overrides, project?.domains]]);
}

export function assignSimulationDevice(current: SimulationEndpoints, role: SimulationEndpointRole, deviceId: string): SimulationEndpoints {
  return role === "source"
    ? { ...current, sourceDeviceId: deviceId, sourceInterfaceId: "" }
    : { ...current, destinationDeviceId: deviceId, destinationInterfaceId: "" };
}

/** Match collapsed bundles through their members without expanding the graph.
 * A hop may reference several member links, but its share is counted only once. */
export function simulationHopsByEdge(edges: GraphData["edges"], simulation: SimulationHighlight) {
  const byLink = new Map<string, Set<SimulationHop>>();
  for (const hop of simulation.hops) for (const id of hop.link_ids) {
    const hops = byLink.get(id) || new Set<SimulationHop>();
    hops.add(hop);
    byLink.set(id, hops);
  }
  const result = new Map<string, SimulationHop[]>();
  for (const edge of edges) {
    const matched = new Set<SimulationHop>();
    for (const id of edge.member_link_ids || [edge.link_id || edge.id])
      for (const hop of byLink.get(id) || []) matched.add(hop);
    if (matched.size) result.set(edge.id, [...matched]);
  }
  return result;
}

export const portAddresses = (iface: Interface) => iface.addresses.filter(address => address.family === 4
  && address.address && address.effective_state === "active" && ["primary", "secondary"].includes(address.role));

export const usablePort = (iface: Interface) => iface.effective_state === "active"
  && iface.admin_state !== "disabled" && !iface.aggregate_interface_id && portAddresses(iface).length > 0;

export function simulationCandidates(project: Project, choice: SimulationEndpoints) {
  const facts = project.snapshot?.routing;
  const deviceIds = new Set(project.snapshot?.devices.map(device => device.id));
  const interfaces = new Map(project.snapshot?.interfaces.map(iface => [iface.id, iface]));
  const declaredInstances = new Map((facts?.instances || []).filter(instance => deviceIds.has(instance.device_id))
    .map(instance => [instance.id, instance]));
  const validBindings = (facts?.bindings || []).filter(binding => {
    const iface = interfaces.get(binding.interface_id);
    const instance = declaredInstances.get(binding.instance_id);
    return iface?.device_id === binding.device_id && instance?.device_id === binding.device_id
      && iface.routing_context_id === instance.routing_context_id;
  });
  const bound = new Set(validBindings.map(binding => binding.instance_id));
  const instances = [...declaredInstances.values()].filter(instance => bound.has(instance.id));
  const portProtocols = new Map<string, Set<RoutingInstance["protocol"]>>();
  for (const binding of validBindings) {
    const protocols = portProtocols.get(binding.interface_id) || new Set<RoutingInstance["protocol"]>();
    protocols.add(declaredInstances.get(binding.instance_id)!.protocol);
    portProtocols.set(binding.interface_id, protocols);
  }
  const ports = [...interfaces.values()].filter(iface => !iface.aggregate_interface_id
    && portProtocols.has(iface.id) && iface.addresses.some(address => address.family === 4 && address.address));
  const sourcePort = ports.find(iface => iface.device_id === choice.sourceDeviceId && iface.id === choice.sourceInterfaceId);
  const destinationPort = ports.find(iface => iface.device_id === choice.destinationDeviceId && iface.id === choice.destinationInterfaceId);
  const supported = (deviceId: string) => new Set(ports.filter(iface => !deviceId || iface.device_id === deviceId)
    .flatMap(iface => [...portProtocols.get(iface.id)!]));
  const from = supported(choice.sourceDeviceId);
  const to = supported(choice.destinationDeviceId);
  // Keep device-level choices available so selecting a port cannot hide the
  // protocol selector and prevent switching to another configured protocol.
  const protocols = (["ospf", "isis"] as const).filter(protocol => from.has(protocol) && to.has(protocol));
  const advertisedBindings = destinationPort ? new Set((facts?.advertisements || [])
    .filter(advertisement => advertisement.interface_id === destinationPort.id).map(advertisement => advertisement.binding_id)) : null;
  const destinationInstances = advertisedBindings ? new Set(validBindings.filter(binding => advertisedBindings.has(binding.id)).map(binding => binding.instance_id)) : null;
  const published = destinationInstances ? new Set(instances.filter(instance => destinationInstances.has(instance.id)).map(instance => instance.protocol)) : null;
  const matching = protocols.filter(protocol => (!sourcePort || portProtocols.get(sourcePort.id)!.has(protocol))
    && (!destinationPort || portProtocols.get(destinationPort.id)!.has(protocol))
    && (!published || published.has(protocol)));
  const protocol = protocols.find(value => value === choice.protocol)
    || (matching.length === 1 ? matching[0] : protocols.length === 1 ? protocols[0] : "");
  const eligible = ports.filter(iface => protocol ? portProtocols.get(iface.id)!.has(protocol)
    : protocols.some(value => portProtocols.get(iface.id)!.has(value)));
  const sourceInterfaces = eligible.filter(iface => iface.device_id === choice.sourceDeviceId);
  const destinationInterfaces = eligible.filter(iface => iface.device_id === choice.destinationDeviceId);
  // Ports always require an explicit choice, including when there is only one.
  const source = sourceInterfaces.find(iface => iface.id === choice.sourceInterfaceId);
  const destination = destinationInterfaces.find(iface => iface.id === choice.destinationInterfaceId);
  return { instances, protocols, protocol, sourceInterfaces, destinationInterfaces, source, destination };
}

export function assignSimulationProtocol(project: Project, current: SimulationEndpoints, protocol: SimulationEndpoints["protocol"]): SimulationEndpoints {
  const next = { ...current, protocol };
  const candidates = simulationCandidates(project, next);
  return { ...next, sourceInterfaceId: candidates.source?.id || "", destinationInterfaceId: candidates.destination?.id || "" };
}
