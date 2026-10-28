import type { Domains, GraphData, Snapshot, View } from "./types";

/** Retain saved layout and filters while retiring the subnet projection from the UI. */
export function deviceView(view: View): View {
  return { ...view, filters: { ...view.filters, mode: "device" } };
}

/** Presentation only: keep the actual links and their review/evidence independent. */
export function parallelLinks(data: GraphData, expanded: ReadonlySet<string>) {
  const devices = new Set(data.nodes.filter((n) => n.kind === "device").map((n) => n.id));
  const groups = new Map<string, GraphData["edges"]>();
  const edges: GraphData["edges"] = [];
  for (const edge of data.edges) {
    if (edge.source === edge.target || !devices.has(edge.source) || !devices.has(edge.target)) {
      edges.push(edge);
      continue;
    }
    const pair = [edge.source, edge.target].sort();
    const id = "bundle:" + JSON.stringify(pair);
    const group = groups.get(id);
    if (group) group.push(edge);
    else groups.set(id, [edge]);
  }
  const bundles = new Map<string, GraphData["edges"]>();
  for (const [id, members] of groups) {
    if (members.length === 1) {
      edges.push(members[0]);
      continue;
    }
    bundles.set(id, members);
    if (expanded.has(id)) {
      edges.push(...members.map((e) => ({ ...e, bundle_id: id })));
      continue;
    }
    const first = members[0];
    edges.push({
      ...first,
      id,
      bundle_id: id,
      link_id: undefined,
      member_ids: members.map((e) => e.id),
      member_link_ids: [...new Set(members.map((e) => e.link_id || e.id))],
      link_count: members.length,
      bundle_width: Math.min(10, 3 + Math.log2(members.length) * 1.5),
      label: `${members.length} 条链路`,
      source_label: "",
      target_label: "",
      origin: members.every((e) => e.origin === first.origin) ? first.origin : "mixed",
      review_state: members.every((e) => e.review_state === first.review_state) ? first.review_state : "mixed",
      prefixes: [...new Set(members.flatMap((e) => e.prefixes || []))],
      families: [...new Set(members.flatMap((e) => e.families || []))],
      evidence: [],
    });
  }
  return { nodes: data.nodes, edges, bundles };
}

/** Derived once per fact/domain revision, never once per device or pan event. */
export function indexTopology(snapshot: Snapshot, domains: Domains) {
  const devices = new Map(snapshot.devices.map((d) => [d.id, d]));
  const interfaces = new Map(snapshot.interfaces.map((i) => [i.id, i]));
  const contexts = new Map(snapshot.contexts.map((c) => [c.id, c]));
  const byDevice = new Map<string, typeof snapshot.interfaces>();
  const searchParts = new Map(
    snapshot.devices.map((d) => [d.id, [d.hostname, d.vendor]]),
  );
  const families = new Map<string, Set<number>>();
  const deviceDomains = new Map<string, Set<string>>();
  const interfaceDomains = new Map<string, string>();
  for (const i of snapshot.interfaces) {
    const list = byDevice.get(i.device_id) || [];
    list.push(i);
    byDevice.set(i.device_id, list);
    const parts = searchParts.get(i.device_id) || [];
    parts.push(i.raw_name, i.normalized_name || "");
    const af = families.get(i.device_id) || new Set<number>();
    for (const a of i.addresses) {
      af.add(a.family);
      parts.push(a.address || "", a.canonical_network || "");
    }
    families.set(i.device_id, af);
    searchParts.set(i.device_id, parts);
    const domain =
      domains.interface_mappings[i.id] ||
      domains.mappings[i.routing_context_id] ||
      "default";
    interfaceDomains.set(i.id, domain);
    const set = deviceDomains.get(i.device_id) || new Set<string>();
    set.add(domain);
    deviceDomains.set(i.device_id, set);
  }
  return {
    devices,
    interfaces,
    contexts,
    byDevice,
    families,
    deviceDomains,
    interfaceDomains,
    search: new Map(
      [...searchParts].map(([id, parts]) => [
        id,
        parts.join(" ").toLowerCase(),
      ]),
    ),
    segments: new Map(snapshot.segments.map((g) => [g.id, g])),
  };
}
export type TopologyIndex = ReturnType<typeof indexTopology>;
export function filterTopology(
  data: GraphData,
  index: TopologyIndex,
  filters: Record<string, string>,
): GraphData {
  const needle = (filters.search || "").toLowerCase();
  const allowed = new Set<string>();
  for (const [id, d] of index.devices) {
    if (
      filters.vendor &&
      filters.vendor !== "all" &&
      d.vendor !== filters.vendor
    )
      continue;
    if (needle && !index.search.get(id)?.includes(needle)) continue;
    if (
      filters.family &&
      filters.family !== "all" &&
      !index.families.get(id)?.has(Number(filters.family))
    )
      continue;
    if (
      filters.domain &&
      filters.domain !== "all" &&
      !index.deviceDomains.get(id)?.has(filters.domain)
    )
      continue;
    allowed.add(id);
  }
  for (const [id, g] of index.segments) {
    if (
      filters.family &&
      filters.family !== "all" &&
      String(g.family) !== filters.family
    )
      continue;
    if (
      filters.domain &&
      filters.domain !== "all" &&
      g.domain_id !== filters.domain
    )
      continue;
    if (
      g.interface_ids.some((id) =>
        allowed.has(index.interfaces.get(id)?.device_id || ""),
      )
    )
      allowed.add(id);
  }
  let nodes = data.nodes.filter((n) => allowed.has(n.id));
  let edges = data.edges.filter(
    (e) =>
      allowed.has(e.source) &&
      allowed.has(e.target) &&
      (!filters.family ||
        filters.family === "all" ||
        e.families?.includes(Number(filters.family))) &&
      (!filters.domain ||
        filters.domain === "all" ||
        e.evidence?.some((ev) => ev.domain_id === filters.domain) ||
        index.interfaceDomains.get(e.interface_a_id) === filters.domain),
  );
  if (filters.focus) {
    const adjacent = new Map<string, Set<string>>();
    for (const e of edges) {
      const a = adjacent.get(e.source) || new Set<string>();
      a.add(e.target);
      adjacent.set(e.source, a);
      const b = adjacent.get(e.target) || new Set<string>();
      b.add(e.source);
      adjacent.set(e.target, b);
    }
    const kept = new Set([
      filters.focus,
      ...(adjacent.get(filters.focus) || []),
    ]);
    for (const id of [...kept])
      if (index.segments.has(id))
        for (const neighbor of adjacent.get(id) || []) kept.add(neighbor);
    nodes = nodes.filter((n) => kept.has(n.id));
    edges = edges.filter((e) => kept.has(e.source) && kept.has(e.target));
  }
  return { nodes, edges };
}

type Point = { x: number; y: number };
/** Linear-time connected-component/BFS ordering and spatially indexed insertion.
 * Existing coordinates are immutable. A reimport only places missing nodes. */
export function placeMissingNodes(
  data: GraphData,
  existing: View["positions"],
): View["positions"] {
  const ids = new Set(data.nodes.map((n) => n.id));
  const result: View["positions"] = {};
  for (const n of data.nodes)
    if (
      existing[n.id] &&
      Number.isFinite(existing[n.id].x) &&
      Number.isFinite(existing[n.id].y)
    )
      result[n.id] = { ...existing[n.id] };
  const missing = data.nodes.filter((n) => !result[n.id]);
  if (!missing.length) return result;
  const adjacency = new Map(data.nodes.map((n) => [n.id, [] as string[]]));
  for (const e of data.edges)
    if (ids.has(e.source) && ids.has(e.target)) {
      adjacency.get(e.source)!.push(e.target);
      adjacency.get(e.target)!.push(e.source);
    }
  const seen = new Set<string>(),
    ordered: string[][] = [];
  for (const n of data.nodes) {
    if (seen.has(n.id)) continue;
    const queue = [n.id];
    seen.add(n.id);
    for (let q = 0; q < queue.length; q++)
      for (const id of adjacency.get(queue[q]) || [])
        if (!seen.has(id)) {
          seen.add(id);
          queue.push(id);
        }
    ordered.push(queue);
  }
  const stepX = 230,
    stepY = 135;
  if (!Object.keys(result).length) {
    const shelfWidth =
      Math.max(4, Math.ceil(Math.sqrt(data.nodes.length))) * stepX;
    let x = 0,
      y = 0,
      shelfHeight = 0;
    for (const component of ordered) {
      const cols = Math.max(1, Math.ceil(Math.sqrt(component.length)));
      const rows = Math.ceil(component.length / cols);
      const width = cols * stepX;
      if (x && x + width > shelfWidth) {
        x = 0;
        y += shelfHeight + stepY;
        shelfHeight = 0;
      }
      component.forEach((id, i) => {
        const row = Math.floor(i / cols);
        const col = row % 2 ? cols - 1 - (i % cols) : i % cols;
        result[id] = { x: x + col * stepX, y: y + row * stepY };
      });
      x += width + stepX;
      shelfHeight = Math.max(shelfHeight, rows * stepY);
    }
    return result;
  }
  const buckets = new Map<string, Point[]>();
  const key = (x: number, y: number) => `${x},${y}`;
  const put = (p: Point) => {
    const k = key(Math.floor(p.x / stepX), Math.floor(p.y / stepY));
    const a = buckets.get(k) || [];
    a.push(p);
    buckets.set(k, a);
  };
  let maxX = 0;
  for (const p of Object.values(result)) {
    put(p);
    maxX = Math.max(maxX, p.x);
  }
  const free = (p: Point) => {
    const x = Math.floor(p.x / stepX),
      y = Math.floor(p.y / stepY);
    for (let dx = -1; dx <= 1; dx++)
      for (let dy = -1; dy <= 1; dy++)
        for (const q of buckets.get(key(x + dx, y + dy)) || [])
          if (Math.abs(p.x - q.x) < 200 && Math.abs(p.y - q.y) < 95)
            return false;
    return true;
  };
  let fallback = 0;
  const fallbackCols = Math.max(1, Math.ceil(Math.sqrt(missing.length)));
  for (const component of ordered)
    for (const id of component) {
      if (result[id]) continue;
      const neighbor = (adjacency.get(id) || []).find((n) => result[n]);
      let point: Point | undefined;
      if (neighbor) {
        const origin = result[neighbor];
        for (let radius = 1; radius <= 8 && !point; radius++)
          for (const [dx, dy] of [
            [1, 0],
            [0, 1],
            [-1, 0],
            [0, -1],
            [1, 1],
            [-1, 1],
            [1, -1],
            [-1, -1],
          ]) {
            const candidate = {
              x: origin.x + dx * radius * stepX,
              y: origin.y + dy * radius * stepY,
            };
            if (free(candidate)) {
              point = candidate;
              break;
            }
          }
      }
      while (!point) {
        const candidate = {
          x: maxX + stepX * 2 + (fallback % fallbackCols) * stepX,
          y: Math.floor(fallback / fallbackCols) * stepY,
        };
        fallback++;
        if (free(candidate)) point = candidate;
      }
      result[id] = point;
      put(point);
    }
  return result;
}
