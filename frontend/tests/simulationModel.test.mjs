import test from "node:test";
import assert from "node:assert/strict";
import { assignSimulationDevice, assignSimulationProtocol, emptySimulationEndpoints, simulationCandidates, simulationContextKey, simulationHopsByEdge, usablePort } from "../src/simulationModel.ts";
import { parallelLinks } from "../src/graphModel.ts";

const bundledGraph = () => ({
  nodes: ["a", "b", "c"].map(id => ({ id, kind: "device" })),
  edges: ["b", "c"].flatMap(target => Array.from({ length: 200 }, (_, i) => ({
    id: `${target}-edge-${i}`, link_id: `${target}-link-${i}`, source: "a", target,
  }))),
});

test("simulation highlights collapsed members and sums ECMP shares without expanding unrelated links", () => {
  const data = bundledGraph();
  const display = parallelLinks(data, new Set());
  const hops = [
    { id: "first", link_ids: ["b-link-4", "b-link-5"], cost: 20, share_percent: 40 },
    { id: "second", link_ids: ["b-link-8"], cost: 20, share_percent: 60 },
  ];
  const highlighted = simulationHopsByEdge(display.edges, { hops });
  assert.equal(display.edges.length, 2);
  assert.equal(highlighted.size, 1);
  const selected = highlighted.get(display.edges.find(edge => edge.target === "b").id);
  assert.deepEqual(selected, hops);
  assert.equal(selected.reduce((sum, hop) => sum + hop.share_percent, 0), 100);
  assert.equal(data.edges.length, 400);
});

test("manual bundle expansion preserves the highlighted physical links and leaves other groups collapsed", () => {
  const data = bundledGraph();
  const bundle = parallelLinks(data, new Set()).edges.find(edge => edge.target === "b");
  const display = parallelLinks(data, new Set([bundle.id]));
  const hop = { id: "route", link_ids: ["b-link-8"], cost: 20, share_percent: 100 };
  const highlighted = simulationHopsByEdge(display.edges, { hops: [hop] });
  assert.equal(display.edges.length, 201);
  assert.deepEqual([...highlighted.keys()], ["b-edge-8"]);
  assert.equal(display.edges.find(edge => edge.target === "c").link_count, 200);
  const collapsed = parallelLinks(data, new Set());
  assert.deepEqual(simulationHopsByEdge(collapsed.edges, { hops: [hop] }).get(bundle.id), [hop]);
});

test("ordinary and observed edges still match their link or edge ID", () => {
  const hops = [{ id: "config", link_ids: ["link"], share_percent: 100 },
    { id: "observed", link_ids: ["observed-edge"], share_percent: 100 }];
  const highlighted = simulationHopsByEdge([
    { id: "config-edge", link_id: "link" }, { id: "observed-edge" }, { id: "unrelated" },
  ], { hops });
  assert.deepEqual([...highlighted.values()], [[hops[0]], [hops[1]]]);
});

const port = (id, device, context = device) => ({ id, device_id: device, routing_context_id: context,
  raw_name: id, kind: "physical", effective_state: "active", admin_state: "enabled",
  addresses: [{ family: 4, address: "10.0.0.1", role: "primary", effective_state: "active" }] });
const fixture = () => ({ id: "project", snapshot_id: "snapshot", effective_revision: "facts-1", version: 1,
  overrides: [], domains: {}, snapshot: {
    devices: [{ id: "a", hostname: "same-name" }, { id: "b", hostname: "same-name" }, { id: "manual", hostname: "Manual" }],
    interfaces: [port("a-ge", "a"), port("b-ge", "b")],
    routing: {
      instances: [{ id: "a-isis", device_id: "a", routing_context_id: "a", protocol: "isis" },
        { id: "b-isis", device_id: "b", routing_context_id: "b", protocol: "isis" }],
      bindings: [{ id: "a-i", device_id: "a", instance_id: "a-isis", interface_id: "a-ge", scope: "2" },
        { id: "b-i", device_id: "b", instance_id: "b-isis", interface_id: "b-ge", scope: "2" }],
      advertisements: [{ id: "b-prefix", device_id: "b", binding_id: "b-i", interface_id: "b-ge", prefix: "10.0.0.0/24" }],
    },
  },
});
const chosen = () => ({ ...emptySimulationEndpoints(), sourceDeviceId: "a", sourceInterfaceId: "a-ge",
  destinationDeviceId: "b", destinationInterfaceId: "b-ge" });

test("no protocol is invented for an old snapshot or configuration without routing", () => {
  const project = fixture();
  project.snapshot.routing = {};
  const candidates = simulationCandidates(project, chosen());
  assert.deepEqual(candidates.protocols, []);
  assert.equal(candidates.protocol, "");
});

test("IS-IS-only configuration never exposes or defaults to OSPF, including stale choices", () => {
  const project = fixture();
  for (const choice of [emptySimulationEndpoints(), chosen(), { ...chosen(), protocol: "ospf" }]) {
    const candidates = simulationCandidates(project, choice);
    assert.deepEqual(candidates.protocols, ["isis"]);
    assert.equal(candidates.protocol, "isis");
  }
  project.snapshot.routing.instances.push({ id: "unbound-ospf", device_id: "a", protocol: "ospf" });
  assert.deepEqual(simulationCandidates(project, emptySimulationEndpoints()).protocols, ["isis"]);
});

test("ports require manual choice even if each device has only one physical port", () => {
  const project = fixture();
  const initial = simulationCandidates(project, { ...chosen(), sourceInterfaceId: "", destinationInterfaceId: "" });
  assert.equal(initial.source, undefined);
  assert.equal(initial.destination, undefined);
  assert.equal(initial.sourceInterfaces.length, 1);
  const selected = simulationCandidates(project, chosen());
  assert.equal(selected.source.id, "a-ge");
  assert.equal(selected.destination.id, "b-ge");
});

test("reassigning an endpoint clears its own port and retains the opposite endpoint", () => {
  const current = chosen();
  const source = assignSimulationDevice(current, "source", "b");
  assert.equal(source.sourceInterfaceId, "");
  assert.equal(source.destinationInterfaceId, "b-ge");
  const destination = assignSimulationDevice(current, "destination", "a");
  assert.equal(destination.sourceInterfaceId, "a-ge");
  assert.equal(destination.destinationInterfaceId, "");
  assert.equal(current.sourceInterfaceId, "a-ge");
});

test("selected ports infer their protocol while other device protocols remain available for switching", () => {
  const project = fixture();
  for (const device of ["a", "b"]) {
    project.snapshot.interfaces.push(port(`${device}-vrf`, device, "vrf"));
    project.snapshot.routing.instances.push({ id: `${device}-ospf`, device_id: device, routing_context_id: "vrf", protocol: "ospf" });
    project.snapshot.routing.bindings.push({ id: `${device}-o`, device_id: device, instance_id: `${device}-ospf`, interface_id: `${device}-vrf`, scope: "0" });
  }
  const initial = simulationCandidates(project, emptySimulationEndpoints());
  assert.deepEqual(initial.protocols, ["ospf", "isis"]);
  assert.equal(initial.protocol, "");
  const selected = simulationCandidates(project, chosen());
  assert.deepEqual(selected.protocols, ["ospf", "isis"]);
  assert.equal(selected.protocol, "isis");
  assert.deepEqual(selected.sourceInterfaces.map(iface => iface.id), ["a-ge"]);
});

test("missing IDs never select another port or another device with the same hostname", () => {
  const project = fixture();
  assert.equal(simulationCandidates(project, { ...chosen(), sourceDeviceId: "manual" }).source, undefined);
  project.snapshot.interfaces[0].id = "replacement";
  assert.equal(simulationCandidates(project, chosen()).source, undefined);
});

test("a protocol on another destination port cannot masquerade as a route to the chosen port", () => {
  const project = fixture();
  for (const device of ["a", "b"]) {
    project.snapshot.routing.instances.push({ id: `${device}-ospf`, device_id: device, routing_context_id: device, protocol: "ospf" });
    project.snapshot.routing.bindings.push({ id: `${device}-o`, device_id: device, instance_id: `${device}-ospf`, interface_id: `${device}-ge`, scope: "0" });
  }
  assert.deepEqual(simulationCandidates(project, emptySimulationEndpoints()).protocols, ["ospf", "isis"]);
  assert.equal(simulationCandidates(project, chosen()).protocol, "isis");
});

test("IPv4 addresses alone never expose an unbound management or destination port", () => {
  const project = fixture();
  for (const device of ["a", "b"]) project.snapshot.interfaces.push(port(`${device}-management`, device));
  const candidates = simulationCandidates(project, chosen());
  assert.deepEqual(candidates.sourceInterfaces.map(iface => iface.id), ["a-ge"]);
  assert.deepEqual(candidates.destinationInterfaces.map(iface => iface.id), ["b-ge"]);
  const stale = simulationCandidates(project, { ...chosen(), sourceInterfaceId: "a-management", destinationInterfaceId: "b-management" });
  assert.equal(stale.source, undefined);
  assert.equal(stale.destination, undefined);
});

test("protocol-enabled passive loopbacks, aggregates and subinterfaces remain selectable", () => {
  const project = fixture();
  for (const [id, kind] of [["loopback", "loopback"], ["lag", "aggregate"], ["lag.1", "subinterface"]]) {
    project.snapshot.interfaces.push({ ...port(id, "a"), kind });
    project.snapshot.routing.bindings.push({ id, instance_id: "a-isis", device_id: "a", interface_id: id, passive: kind === "loopback" });
  }
  assert.deepEqual(simulationCandidates(project, chosen()).sourceInterfaces.map(iface => iface.id), ["a-ge", "loopback", "lag", "lag.1"]);
});

const dualProtocolFixture = () => {
  const project = fixture();
  for (const device of ["a", "b"]) {
    project.snapshot.interfaces.push(port(`${device}-ospf-port`, device), port(`${device}-dual`, device));
    project.snapshot.routing.instances.push({ id: `${device}-ospf`, device_id: device, routing_context_id: device, protocol: "ospf" });
    for (const [id, protocol] of [[`${device}-ospf-port`, "ospf"], [`${device}-dual`, "isis"], [`${device}-dual`, "ospf"]]) {
      const bindingId = `${id}-${protocol}`;
      project.snapshot.routing.bindings.push({ id: bindingId, device_id: device, instance_id: `${device}-${protocol}`, interface_id: id });
      project.snapshot.routing.advertisements.push({ id: bindingId, device_id: device, binding_id: bindingId, interface_id: id });
    }
  }
  return project;
};

test("switching protocols filters both lists and clears incompatible ports without replacing devices", () => {
  const project = dualProtocolFixture();
  const current = chosen();
  const next = assignSimulationProtocol(project, current, "ospf");
  assert.deepEqual(next, { ...current, protocol: "ospf", sourceInterfaceId: "", destinationInterfaceId: "" });
  const candidates = simulationCandidates(project, next);
  assert.deepEqual(candidates.sourceInterfaces.map(iface => iface.id), ["a-ospf-port", "a-dual"]);
  assert.deepEqual(candidates.destinationInterfaces.map(iface => iface.id), ["b-ospf-port", "b-dual"]);
  assert.equal(candidates.source, undefined);
  assert.equal(candidates.destination, undefined);
  assert.equal(current.sourceInterfaceId, "a-ge");
  const back = assignSimulationProtocol(project, next, "isis");
  assert.deepEqual(simulationCandidates(project, back).sourceInterfaces.map(iface => iface.id), ["a-ge", "a-dual"]);
});

test("protocol changes keep compatible selections on ports enabled for both protocols", () => {
  const project = dualProtocolFixture();
  const current = { ...chosen(), sourceInterfaceId: "a-dual", destinationInterfaceId: "b-dual" };
  const next = assignSimulationProtocol(project, current, "ospf");
  assert.deepEqual(next, { ...current, protocol: "ospf" });
  assert.equal(simulationCandidates(project, next).source.id, "a-dual");
});

test("ports for protocols absent from the other endpoint are excluded", () => {
  const project = dualProtocolFixture();
  project.snapshot.routing.bindings = project.snapshot.routing.bindings.filter(binding => binding.instance_id !== "b-ospf");
  const candidates = simulationCandidates(project, chosen());
  assert.deepEqual(candidates.protocols, ["isis"]);
  assert.deepEqual(candidates.sourceInterfaces.map(iface => iface.id), ["a-ge", "a-dual"]);
});

test("bindings from another device, context or missing instance cannot enable a port", () => {
  const project = fixture();
  for (const [id, instance, context] of [["wrong-device", "b-isis", "a"], ["wrong-context", "a-isis", "vrf"], ["missing-instance", "missing", "a"]]) {
    project.snapshot.interfaces.push(port(id, "a", context));
    project.snapshot.routing.bindings.push({ id, device_id: "a", interface_id: id, instance_id: instance });
  }
  assert.deepEqual(simulationCandidates(project, chosen()).sourceInterfaces.map(iface => iface.id), ["a-ge"]);
});

test("enabled ports without an advertised destination still expose the configured protocol for engine diagnostics", () => {
  const project = fixture();
  project.snapshot.routing.advertisements = [];
  const candidates = simulationCandidates(project, chosen());
  assert.equal(candidates.protocol, "isis");
  assert.equal(candidates.destination.id, "b-ge");
});

test("aggregate members, inactive ports and unknown/dynamic addresses cannot be used", () => {
  assert.equal(usablePort(port("ge", "a")), true);
  for (const change of [{ aggregate_interface_id: "lag" }, { effective_state: "inactive" },
    { admin_state: "disabled" }, { addresses: [] }])
    assert.equal(usablePort({ ...port("ge", "a"), ...change }), false);
  const project = fixture();
  project.snapshot.interfaces[0].aggregate_interface_id = "lag";
  assert.deepEqual(simulationCandidates(project, chosen()).sourceInterfaces, []);
});

test("context changes with effective facts, project or snapshot while view saves retain it", () => {
  const project = fixture();
  const current = simulationContextKey(project);
  assert.equal(simulationContextKey({ ...project, version: 2, views: [{ id: "new-view" }] }), current);
  for (const change of [{ effective_revision: "facts-2" }, { id: "other" }, { snapshot_id: "new-snapshot" }])
    assert.notEqual(simulationContextKey({ ...project, ...change }), current);
});
