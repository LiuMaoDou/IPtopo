import type { GraphData, Interface } from "./types";

/** Only show addresses that participate in this relationship, never another subnet on the port. */
export function interfaceLinkLabel(iface: Interface | undefined, prefixes: string[] = [], families: number[] = []) {
  if (!iface) return "";
  const addresses = iface.addresses.filter(address => address.address
    && address.effective_state !== "inactive"
    && (!families.length || families.includes(address.family))
    && (!prefixes.length || prefixes.includes(address.canonical_network || "")))
    .sort((a, b) => a.family - b.family);
  return [iface.raw_name, ...new Set(addresses.map(address =>
    `IPv${address.family} ${address.address}${address.prefix_length == null ? "" : `/${address.prefix_length}`}`))].join("\n");
}

/** Align by device ownership, including reversed links and device-to-segment projections. */
export function linkEndpointLabels(edges: GraphData["edges"], interfaces: ReadonlyMap<string, Interface>) {
  return new Map(edges.map(edge => {
    const candidates = [interfaces.get(edge.interface_a_id), interfaces.get(edge.interface_b_id)];
    const atEnd = (side: "source" | "target") => edge.link_count ? "" : interfaceLinkLabel(
      candidates.find(iface => iface?.device_id === edge[side]), edge.prefixes, edge.families);
    return [edge.id, { source: atEnd("source"), target: atEnd("target") }];
  }));
}

/** Wrap long IPv6 labels at colons, retaining every character of the address. */
export function canvasEndpointLabel(label: string) {
  return label.split("\n").flatMap(line => {
    const parts: string[] = [];
    while (line.startsWith("IPv6 ") || (parts.length && line.length > 22)) {
      if (line.length <= 22) break;
      const split = line.lastIndexOf(":", 21);
      if (split < 1) break;
      parts.push(line.slice(0, split + 1));
      line = line.slice(split + 1);
    }
    return [...parts, line];
  }).join("\n");
}
