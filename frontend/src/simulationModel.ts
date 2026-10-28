import type { Project } from "./types";
import type { RoutingInstance } from "./routingTypes";

export type SimulationEndpointRole = "source" | "destination";
export type SimulationEndpoints = {
  protocol: RoutingInstance["protocol"];
  sourceDeviceId: string;
  sourceInstanceId: string;
  scope: string;
  destinationDeviceId: string;
  destinationAdvertisementId: string;
};

export const emptySimulationEndpoints = (): SimulationEndpoints => ({
  protocol: "ospf", sourceDeviceId: "", sourceInstanceId: "", scope: "",
  destinationDeviceId: "", destinationAdvertisementId: "",
});

export function simulationContextKey(project: Project | null): string {
  return JSON.stringify([project?.id, project?.snapshot_id,
    project?.effective_revision || [project?.overrides, project?.domains]]);
}

export function assignSimulationDevice(current: SimulationEndpoints, role: SimulationEndpointRole, deviceId: string): SimulationEndpoints {
  return role === "source"
    ? { ...current, sourceDeviceId: deviceId, sourceInstanceId: "" }
    : { ...current, destinationDeviceId: deviceId, destinationAdvertisementId: "" };
}

// Only a unique candidate can be inferred. Never fall back to the first item in a
// different device, process or Loopback when a previously selected ID disappears.
function uniqueOrSelected<T extends { id: string }>(items: T[], id: string): T | undefined {
  return items.find(item => item.id === id) || (items.length === 1 ? items[0] : undefined);
}

export function simulationCandidates(project: Project, choice: SimulationEndpoints) {
  const facts = project.snapshot?.routing;
  const deviceIds = new Set(project.snapshot?.devices.map(device => device.id));
  const interfaces = new Map(project.snapshot?.interfaces.map(iface => [iface.id, iface]));
  const validBindings = (facts?.bindings || []).filter(binding => deviceIds.has(binding.device_id) && interfaces.has(binding.interface_id));
  const bound = new Set(validBindings.map(binding => binding.instance_id));
  const instances = (facts?.instances || []).filter(instance => instance.protocol === choice.protocol && bound.has(instance.id) && deviceIds.has(instance.device_id));
  const sourceInstances = instances.filter(instance => instance.device_id === choice.sourceDeviceId);
  const source = uniqueOrSelected(sourceInstances, choice.sourceInstanceId);
  const scopes = [...new Set(validBindings.filter(binding => binding.instance_id === source?.id).map(binding => binding.scope))].sort();
  const scope = scopes.includes(choice.scope) ? choice.scope : scopes.length === 1 ? scopes[0] : "";
  const instanceIds = new Set(instances.map(instance => instance.id));
  const bindings = scope ? validBindings.filter(binding => instanceIds.has(binding.instance_id) && binding.scope === scope) : [];
  const bindingIds = new Set(bindings.map(binding => binding.id));
  const advertisements = (facts?.advertisements || []).filter(advertisement => bindingIds.has(advertisement.binding_id)
    && deviceIds.has(advertisement.device_id) && advertisement.prefix.endsWith("/32") && interfaces.get(advertisement.interface_id)?.kind === "loopback");
  const destinationAdvertisements = advertisements.filter(advertisement => advertisement.device_id === choice.destinationDeviceId);
  const destination = uniqueOrSelected(destinationAdvertisements, choice.destinationAdvertisementId);
  return { instances, sourceInstances, source, scopes, scope, bindings, bindingIds, advertisements, destinationAdvertisements, destination };
}
