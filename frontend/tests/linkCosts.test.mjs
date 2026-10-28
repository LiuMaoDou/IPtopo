import test from "node:test";
import assert from "node:assert/strict";
import { indexInterfaceCosts, linkCostLabels } from "../src/linkCosts.ts";
import { parallelLinks } from "../src/graphModel.ts";

const binding = (id, cost, protocol = "isis", scope = "2") => ({
  id: `${id}-${protocol}-${scope}`, interface_id: id, device_id: id[0],
  instance_id: `${id[0]}-${protocol}`, cost, scope,
});
const snapshot = bindings => ({
  interfaces: ["a1", "a2", "b1", "b2"].map(id => ({ id, device_id: id[0], routing_context_id: id[0] })),
  routing: {
    instances: ["a", "b"].flatMap(device => ["isis", "ospf"].map(protocol => ({
      id: `${device}-${protocol}`, device_id: device, routing_context_id: device, protocol,
    }))),
    bindings,
  },
});
const edge = (a = "a1", b = "b1") => ({ id: `${a}-${b}`, source: a[0], target: b[0],
  device_a_id: a[0], device_b_id: b[0], interface_a_id: a, interface_b_id: b });
const labels = (facts, edges = [edge()]) => linkCostLabels(edges, new Map(), indexInterfaceCosts(facts));

test("equal metrics share a center label; asymmetric metrics stay on their own outbound ends", () => {
  assert.deepEqual(labels(snapshot([binding("a1", 10), binding("b1", 20)])).get("a1-b1"),
    { center: "", source: "Cost 10", target: "Cost 20" });
  assert.deepEqual(labels(snapshot([binding("a1", 10), binding("b1", 10)])).get("a1-b1"),
    { center: "Cost 10", source: "", target: "" });
});

test("reversed bundle members keep their device's direction without expanding the bundle", () => {
  const facts = snapshot([binding("a1", 10), binding("a2", 10), binding("b1", 20), binding("b2", 20)]);
  const data = { nodes: ["a", "b"].map(id => ({ id, kind: "device" })), edges: [edge(), edge("b2", "a2")] };
  const original = structuredClone({ data, facts });
  const display = parallelLinks(data, new Set());
  const costs = indexInterfaceCosts(facts);
  assert.equal(display.edges.length, 1);
  assert.deepEqual(linkCostLabels(display.edges, display.bundles, costs).get(display.edges[0].id),
    { center: "", source: "Cost 10", target: "Cost 20" });
  const expanded = parallelLinks(data, new Set([display.edges[0].id]));
  assert.deepEqual(linkCostLabels(expanded.edges, expanded.bundles, costs).get("b2-a2"),
    { center: "", source: "Cost 20", target: "Cost 10" });
  assert.deepEqual({ data, facts }, original);
});

test("unknown metrics remain unknown and interfaces without protocol bindings get no invented cost", () => {
  assert.deepEqual(labels(snapshot([binding("a1", null)])).get("a1-b1"),
    { center: "", source: "Cost 待确认", target: "" });
  assert.equal(labels(snapshot([])).size, 0);
  assert.equal(labels(null).size, 0);
  assert.equal(labels(snapshot([binding("a1", 0)])).get("a1-b1").source, "Cost 0");
});

test("different endpoint protocols and unequal scope metrics are not conflated", () => {
  assert.deepEqual(labels(snapshot([binding("a1", 10), binding("b1", 10, "ospf", "0")])).get("a1-b1"),
    { center: "", source: "IS-IS Cost 10", target: "OSPF Cost 10" });
  const facts = snapshot([binding("a1", 10, "isis", "1"), binding("a1", 20, "isis", "2"), binding("a1", 5, "ospf", "0")]);
  assert.equal(labels(facts).get("a1-b1").source, "IS-IS L1 Cost 10\nIS-IS L2 Cost 20\nOSPF Cost 5");
});

test("bundle summaries expose ranges and unresolved members rather than choosing a minimum", () => {
  const facts = snapshot([10, 20, 30, 10000, null].map((cost, i) => ({ ...binding("a1", cost), id: `instance-${i}` })));
  assert.equal(labels(facts).get("a1-b1").source, "Cost 10–10000 / 待确认");
  assert.equal(labels(snapshot([binding("a1", 10), binding("a1", 20)])).get("a1-b1").source, "Cost 10 / 20");
});

test("shared segment projections expose only the real device's interface cost", () => {
  const member = { ...edge(), id: "segment-member", target: "segment", device_b_id: "segment", interface_b_id: null };
  assert.deepEqual(labels(snapshot([binding("a1", 10), binding("b1", 99)]), [member]).get(member.id),
    { center: "", source: "Cost 10", target: "" });
});

test("IPv6-only relationships never inherit IPv4 IGP costs, including inside a bundle", () => {
  const facts = snapshot([binding("a1", 10), binding("b1", 10), binding("a2", 999), binding("b2", 999)]);
  const v4 = { ...edge(), families: [4] }, v6 = { ...edge("a2", "b2"), families: [6] };
  assert.equal(labels(facts, [v6]).size, 0);
  const data = { nodes: ["a", "b"].map(id => ({ id, kind: "device" })), edges: [v4, v6] };
  const display = parallelLinks(data, new Set());
  assert.equal(linkCostLabels(display.edges, display.bundles, indexInterfaceCosts(facts)).get(display.edges[0].id).center, "Cost 10");
});

test("wrong-device, missing-instance and stale-context bindings cannot label a link", () => {
  const facts = snapshot([{ ...binding("a1", 999), instance_id: "b-isis" },
    { ...binding("a2", 999), instance_id: "missing" }, binding("b1", 999)]);
  facts.interfaces.find(iface => iface.id === "b1").routing_context_id = "other-vrf";
  assert.equal(labels(facts).size, 0);
});

test("updated effective routing costs replace old values without changing the topology", () => {
  const graph = [edge()];
  const facts = snapshot([binding("a1", 20), binding("b1", 20)]);
  assert.equal(labels(facts, graph).get("a1-b1").center, "Cost 20");
  const updated = structuredClone(facts);
  updated.routing.bindings[0].cost = 10;
  assert.deepEqual(labels(updated, graph).get("a1-b1"), { center: "", source: "Cost 10", target: "Cost 20" });
});
