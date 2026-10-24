import test from "node:test";
import assert from "node:assert/strict";
import {
  placeMissingNodes,
  indexTopology,
  filterTopology,
  parallelLinks,
} from "../src/graphModel.ts";

test("parallel links collapse without losing direction, status, members or dual-stack identity", () => {
  const data = {
    nodes: [{id:"a",kind:"device"},{id:"b",kind:"device"},{id:"s",kind:"segment"}],
    edges: [
      {id:"one",source:"a",target:"b",origin:"inferred",review_state:"confirmed",prefixes:["10.0.0.0/31","2001:db8::/127"],families:[4,6]},
      {id:"two",source:"b",target:"a",origin:"manual",review_state:"pending",prefixes:["10.0.1.0/31"],families:[4]},
      {id:"member",source:"a",target:"s"},
      {id:"member2",source:"a",target:"s"},
    ],
  };
  const original = structuredClone(data);
  const collapsed = parallelLinks(data,new Set());
  assert.equal(collapsed.edges.length,3);
  const bundle = collapsed.edges.find(e=>e.link_count);
  assert.equal(bundle.link_count,2);
  assert.ok(bundle.bundle_width>3);
  assert.equal(bundle.review_state,"mixed");
  assert.equal(bundle.origin,"mixed");
  assert.deepEqual(bundle.member_link_ids,["one","two"]);
  const expanded = parallelLinks(data,new Set([bundle.id]));
  assert.equal(expanded.edges.length,4);
  assert.equal(expanded.edges.find(e=>e.id==="two").source,"b");
  assert.equal(expanded.edges.find(e=>e.id==="two").review_state,"pending");
  assert.deepEqual(data,original);
  const filtered = parallelLinks({...data,edges:[data.edges[0]]},new Set());
  assert.equal(filtered.edges[0].id,"one");
  assert.equal(filtered.bundles.size,0);
  assert.equal(parallelLinks({...data,edges:[...data.edges].reverse()},new Set()).edges.find(e=>e.link_count).id,bundle.id);
});

test("1000 devices with three parallel links per pair reduce to one edge per pair", () => {
  const data = graph(1000);
  data.edges = data.edges.flatMap(edge=>[0,1,2].map(n=>({...edge,id:`${edge.id}-${n}`})));
  const result = parallelLinks(data,new Set());
  assert.equal(result.edges.length,999);
  assert.equal(result.nodes.length,1000);
  assert.ok(result.edges.every(e=>e.link_count===3 && e.member_ids.length===3));
  assert.equal(data.edges.length,2997);
});
function graph(n) {
  return {
    nodes: Array.from({ length: n }, (_, i) => ({
      id: `d${i}`,
      label: `Device ${i}`,
      kind: "device",
    })),
    edges: Array.from({ length: Math.max(0, n - 1) }, (_, i) => ({
      id: `e${i}`,
      source: `d${i}`,
      target: `d${i + 1}`,
      families: [4],
      evidence: [],
      interface_a_id: `i${i}`,
    })),
  };
}
for (const size of [100, 1000, 2000])
  test(`deterministic nonoverlapping ${size}-node initial placement`, () => {
    const data = graph(size),
      a = placeMissingNodes(data, {}),
      b = placeMissingNodes(data, {});
    assert.deepEqual(a, b);
    assert.equal(Object.keys(a).length, size);
    assert.equal(
      new Set(Object.values(a).map((p) => `${p.x},${p.y}`)).size,
      size,
    );
  });
test("reimport only places new nodes; arbitrary old and pinned coordinates remain exact", () => {
  const old = placeMissingNodes(graph(1000), {});
  old.d5 = { x: 123.456, y: -87.25 };
  const next = placeMissingNodes(graph(1100), old);
  for (const [id, p] of Object.entries(old)) assert.deepEqual(next[id], p);
  assert.equal(Object.keys(next).length, 1100);
  const points = Object.values(next);
  assert.equal(new Set(points.map((p) => `${p.x},${p.y}`)).size, 1100);
});
test("disconnected components have finite distinct positions", () => {
  const data = graph(1000);
  data.edges = [];
  const p = placeMissingNodes(data, {});
  assert.equal(
    new Set(Object.values(p).map((v) => `${v.x},${v.y}`)).size,
    1000,
  );
  assert.ok(
    Object.values(p).every((v) => Number.isFinite(v.x) && Number.isFinite(v.y)),
  );
});
test("indexed AF/domain/prefix filters and shared-segment neighborhood retain exact members", () => {
  const devices = ["a", "b", "c"].map((id) => ({
    id,
    hostname: id,
    vendor: "Cisco",
  }));
  const snapshot = {
    devices,
    contexts: devices.map((d) => ({
      id: `c${d.id}`,
      device_id: d.id,
      kind: "default",
    })),
    interfaces: devices.map((d, i) => ({
      id: `i${d.id}`,
      device_id: d.id,
      routing_context_id: `c${d.id}`,
      raw_name: "Gi0/0",
      normalized_name: "GigabitEthernet0/0",
      addresses: [
        {
          family: 4,
          address: `10.0.0.${i + 1}`,
          canonical_network: "10.0.0.0/24",
        },
      ],
    })),
    segments: [
      { id: "s", domain_id: "default", family: 4, interface_ids: ["ia", "ib"] },
    ],
    links: [],
  };
  const index = indexTopology(snapshot, {
    mappings: {},
    interface_mappings: {},
  });
  const data = {
    nodes: [
      ...devices.map((d) => ({ ...d, label: d.id, kind: "device" })),
      { id: "s", label: "10.0.0.0/24", kind: "segment" },
    ],
    edges: [
      ["a", "s"],
      ["b", "s"],
      ["b", "c"],
    ].map(([source, target], i) => ({
      id: String(i),
      source,
      target,
      families: [4],
      evidence: [{ domain_id: "default" }],
      interface_a_id: `i${source}`,
    })),
  };
  const focused = filterTopology(data, index, {
    family: "4",
    domain: "default",
    focus: "a",
  });
  assert.deepEqual(focused.nodes.map((n) => n.id).sort(), ["a", "b", "s"]);
  assert.equal(focused.edges.length, 2);
  assert.equal(
    filterTopology(data, index, { search: "10.0.0.0/24" }).nodes.length,
    4,
  );
  assert.equal(filterTopology(data, index, { family: "6" }).nodes.length, 0);
});
