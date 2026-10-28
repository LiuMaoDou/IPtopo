import test from "node:test";
import assert from "node:assert/strict";
import { canvasEndpointLabel, interfaceLinkLabel, linkEndpointLabels } from "../src/linkEndpoints.ts";

const address = (family, address, network, length) => ({ family, address, canonical_network: network, prefix_length: length, effective_state: "active" });
const a = { id: "a1", device_id: "a", raw_name: "GE0/1", addresses: [
  address(4, "10.0.0.0", "10.0.0.0/31", 31), address(6, "2001:db8::2", "2001:db8::2/127", 127),
  address(4, "192.0.2.1", "192.0.2.0/24", 24),
] };
const b = { id: "b1", device_id: "b", raw_name: "Eth-Trunk0.1", addresses: [
  address(4, "10.0.0.1", "10.0.0.0/31", 31), address(6, "2001:db8::3", "2001:db8::2/127", 127),
] };
const edge = { id: "e", source: "a", target: "b", interface_a_id: "a1", interface_b_id: "b1",
  families: [4, 6], prefixes: ["10.0.0.0/31", "2001:db8::2/127"] };
const interfaces = new Map([[a.id, a], [b.id, b]]);
test("dual-stack endpoint labels pair each real address with its interface and omit other subnets", () => {
  assert.deepEqual(linkEndpointLabels([edge], interfaces).get("e"), {
    source: "GE0/1\nIPv4 10.0.0.0/31\nIPv6 2001:db8::2/127",
    target: "Eth-Trunk0.1\nIPv4 10.0.0.1/31\nIPv6 2001:db8::3/127",
  });
});
test("reversed edges use device ownership, not interface array order", () => {
  const normal = linkEndpointLabels([edge], interfaces).get("e");
  assert.deepEqual(linkEndpointLabels([{ ...edge, source: "b", target: "a" }], interfaces).get("e"),
    { source: normal.target, target: normal.source });
});
test("segment projections label only the actual device and matching address family", () => {
  assert.deepEqual(linkEndpointLabels([{ ...edge, target: "segment", interface_b_id: null, families: [6], prefixes: ["2001:db8::2/127"] }], interfaces).get("e"),
    { source: "GE0/1\nIPv6 2001:db8::2/127", target: "" });
});
test("collapsed bundles never pretend their first member represents all interfaces", () => {
  assert.deepEqual(linkEndpointLabels([{ ...edge, link_count: 3 }], interfaces).get("e"), { source: "", target: "" });
});
test("missing addresses never fall back to a subnet or peer's address", () => {
  assert.equal(interfaceLinkLabel(a, ["203.0.113.0/24"]), "GE0/1");
  assert.equal(interfaceLinkLabel(undefined, edge.prefixes), "");
  assert.equal(interfaceLinkLabel({ ...a, addresses: [ { ...a.addresses[0], effective_state: "inactive" }, { ...a.addresses[1], address: null } ] }), "GE0/1");
});
test("long IPv6 addresses wrap at colons without truncation or changing their value", () => {
  const address = "IPv6 2001:0db8:7004:7100:1000::2/127";
  const wrapped = canvasEndpointLabel(address);
  assert.equal(wrapped.replaceAll("\n", ""), address);
  assert.ok(wrapped.split("\n").slice(0, -1).every(line => line.endsWith(":")));
  assert.equal(canvasEndpointLabel("GE0/1\nIPv4 10.0.0.1/31"), "GE0/1\nIPv4 10.0.0.1/31");
});
