import test from "node:test";
import assert from "node:assert/strict";
import { assignSimulationDevice, emptySimulationEndpoints, simulationCandidates, simulationContextKey } from "../src/simulationModel.ts";

const fixture = () => ({
  id: "project", snapshot_id: "snapshot", effective_revision: "facts-1", version: 1,
  overrides: [], domains: {},
  snapshot: {
    devices: [{ id: "device-a", hostname: "same-name" }, { id: "device-b", hostname: "same-name" }, { id: "manual", hostname: "Manual" }],
    interfaces: [{ id: "a-lo", device_id: "device-a", kind: "loopback" }, { id: "b-lo", device_id: "device-b", kind: "loopback" }, { id: "b-ge", device_id: "device-b", kind: "physical" }],
    routing: {
      instances: [
        { id: "a-ospf-1", device_id: "device-a", protocol: "ospf" },
        { id: "a-ospf-2", device_id: "device-a", protocol: "ospf" },
        { id: "a-isis", device_id: "device-a", protocol: "isis" },
        { id: "b-ospf", device_id: "device-b", protocol: "ospf" },
      ],
      bindings: [
        { id: "a-o1", device_id: "device-a", instance_id: "a-ospf-1", interface_id: "a-lo", scope: "0.0.0.0" },
        { id: "a-o2", device_id: "device-a", instance_id: "a-ospf-2", interface_id: "a-lo", scope: "0.0.0.1" },
        { id: "a-i", device_id: "device-a", instance_id: "a-isis", interface_id: "a-lo", scope: "2" },
        { id: "b-o", device_id: "device-b", instance_id: "b-ospf", interface_id: "b-lo", scope: "0.0.0.0" },
        { id: "b-ge-o", device_id: "device-b", instance_id: "b-ospf", interface_id: "b-ge", scope: "0.0.0.0" },
      ],
      advertisements: [
        { id: "loopback-1", device_id: "device-b", binding_id: "b-o", interface_id: "b-lo", prefix: "10.0.0.1/32", address: "10.0.0.1" },
        { id: "loopback-2", device_id: "device-b", binding_id: "b-o", interface_id: "b-lo", prefix: "10.0.0.2/32", address: "10.0.0.2" },
        { id: "not-loopback", device_id: "device-b", binding_id: "b-ge-o", interface_id: "b-ge", prefix: "10.0.0.3/32" },
        { id: "not-host-route", device_id: "device-b", binding_id: "b-o", interface_id: "b-lo", prefix: "10.0.1.0/24" },
      ],
    },
  },
});

const chosen = () => ({ ...emptySimulationEndpoints(), sourceDeviceId: "device-a", sourceInstanceId: "a-ospf-1", destinationDeviceId: "device-b" });

test("canvas endpoint intent retains the other endpoint and current protocol/scope with exact device IDs", () => {
  const current = { ...chosen(), scope: "0.0.0.0", destinationAdvertisementId: "loopback-2" };
  const source = assignSimulationDevice(current, "source", "device-b");
  assert.equal(source.sourceDeviceId, "device-b");
  assert.equal(source.sourceInstanceId, "");
  assert.equal(source.destinationAdvertisementId, "loopback-2");
  assert.equal(source.scope, "0.0.0.0");
  assert.equal(source.protocol, "ospf");
  const destination = assignSimulationDevice(current, "destination", "device-a");
  assert.equal(destination.sourceInstanceId, "a-ospf-1");
  assert.equal(destination.destinationAdvertisementId, "");
  assert.equal(current.destinationAdvertisementId, "loopback-2");
});

test("multiple source instances and Loopbacks stay unselected until the user resolves ambiguity", () => {
  const project = fixture();
  const initial = simulationCandidates(project, { ...chosen(), sourceInstanceId: "" });
  assert.deepEqual(initial.sourceInstances.map(instance => instance.id), ["a-ospf-1", "a-ospf-2"]);
  assert.equal(initial.source, undefined);
  const resolved = simulationCandidates(project, chosen());
  assert.equal(resolved.source.id, "a-ospf-1");
  assert.equal(resolved.scope, "0.0.0.0");
  assert.deepEqual(resolved.destinationAdvertisements.map(item => item.id), ["loopback-1", "loopback-2"]);
  assert.equal(resolved.destination, undefined);
  assert.equal(simulationCandidates(project, { ...chosen(), destinationAdvertisementId: "loopback-2" }).destination.id, "loopback-2");
});

test("unique candidates resolve only inside the selected device, protocol and scope", () => {
  const project = fixture();
  project.snapshot.routing.advertisements = project.snapshot.routing.advertisements.slice(0, 1);
  const result = simulationCandidates(project, { ...chosen(), sourceDeviceId: "device-b", sourceInstanceId: "" });
  assert.equal(result.source.id, "b-ospf");
  assert.equal(result.destination.id, "loopback-1");
  assert.equal(result.source.device_id, "device-b");
  const isis = simulationCandidates(project, { ...chosen(), protocol: "isis", sourceInstanceId: "" });
  assert.equal(isis.source.id, "a-isis");
  assert.equal(isis.scope, "2");
  assert.equal(isis.destination, undefined);
});

test("multiple protocol scopes require an explicit scope without silently choosing the first", () => {
  const project = fixture();
  project.snapshot.routing.bindings.push({ ...project.snapshot.routing.bindings[0], id: "a-area-2", scope: "0.0.0.2" });
  const result = simulationCandidates(project, chosen());
  assert.deepEqual(result.scopes, ["0.0.0.0", "0.0.0.2"]);
  assert.equal(result.scope, "");
  assert.equal(result.destination, undefined);
  assert.equal(simulationCandidates(project, { ...chosen(), scope: "0.0.0.0" }).scope, "0.0.0.0");
});

test("manual devices and replaced facts never fall back to another device with the same hostname", () => {
  const project = fixture();
  assert.equal(simulationCandidates(project, { ...chosen(), sourceDeviceId: "manual" }).source, undefined);
  project.snapshot.devices[0] = { id: "replacement-id", hostname: "same-name" };
  const result = simulationCandidates(project, chosen());
  assert.equal(result.source, undefined);
  assert.deepEqual(result.sourceInstances, []);
});

test("simulation context changes with effective facts, project or snapshot, while view saves retain it", () => {
  const project = fixture();
  const current = simulationContextKey(project);
  assert.equal(simulationContextKey({ ...project, version: 2, views: [{ id: "new-view" }] }), current);
  for (const change of [{ effective_revision: "facts-2" }, { id: "other-project" }, { snapshot_id: "new-snapshot" }])
    assert.notEqual(simulationContextKey({ ...project, ...change }), current);
  const legacy = { ...project, effective_revision: undefined };
  assert.notEqual(simulationContextKey({ ...legacy, overrides: [{ id: "edit" }] }), simulationContextKey(legacy));
});
