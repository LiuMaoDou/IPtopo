"""Explainable exact-prefix inference. Configuration relations are never physical claims."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import ipaddress
import json

VERSION = "1.1.0"


def stable_id(kind: str, *parts) -> str:
    material = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{kind}-{sha256(material.encode()).hexdigest()[:20]}"


def _refs(items):
    result, seen = [], set()
    for item in items:
        for ref in item.get("source_refs", []):
            clean = {k: v for k, v in ref.items() if k != "text"}
            key = json.dumps(clean, sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append(clean)
    return result


def _diagnostic(code, message, **details):
    return {"id": stable_id("diag", code, details), "severity": "warning", "code": code,
            "message": message, "details": details, "stage": "inference"}


def _domain(interface, contexts, settings):
    context_id = interface.get("routing_context_id")
    context = contexts.get(context_id, {})
    explicit = settings.get("interface_mappings", {}).get(interface["id"])
    explicit = explicit or settings.get("mappings", {}).get(context_id)
    if explicit:
        return explicit, False
    if context_id and context_id not in contexts:
        return f"isolated:{interface['device_id']}:{context_id}", False
    if context.get("kind") == "vrf" or (context.get("local_name", "default").lower() not in ("default", "global", "master", "base")):
        return f"isolated:{context_id or interface['device_id']}", False
    return "default", True



def _member_evidence(evidence, interface, segment_id):
    """A spoke carries one endpoint; the segment owns complete group evidence."""
    iid = interface["id"]
    own_addresses = [a for a in evidence.get("addresses", []) if a["interface_id"] == iid]
    literals = {a["address"] for a in own_addresses}
    facts = [interface] + [a for a in interface.get("addresses", []) if a.get("address") in literals]
    return {"rule": evidence["rule"], "domain_id": evidence["domain_id"], "family": evidence["family"],
            "prefix": evidence["prefix"], "interface_ids": [iid], "source_refs": _refs(facts),
            "addresses": own_addresses, "parser_versions": evidence.get("parser_versions", {}),
            "inference_version": evidence.get("inference_version", VERSION), "segment_id": segment_id,
            "membership_digest": evidence["membership_digest"]}


def _member_diagnostic(diagnostic, segment_id, interface_id):
    return {key: value for key, value in diagnostic.items() if key != "details"} | {
        "details": {"segment_id": segment_id, "interface_id": interface_id, "diagnostic_ref": diagnostic["id"]}}


def _excluded(interface, address, ip_interface):
    if interface.get("kind", "").lower() in ("loopback", "lo"):
        return "loopback"
    if interface.get("admin_state") == "disabled" or interface.get("effective_state") == "inactive" or address.get("effective_state") == "inactive":
        return "inactive"
    if interface.get("effective_state") == "unresolved" or address.get("effective_state") == "unresolved":
        return "unresolved"
    if address.get("acquisition_method") in ("dhcp", "slaac", "unnumbered", "derived", "unresolved"):
        return "unresolved_or_borrowed_address"
    if address.get("role") in ("virtual", "anycast", "link_local"):
        return address["role"]
    ip = ip_interface.ip
    if ip.is_link_local:
        return "link_local"
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
        return "special_address"
    if ip_interface.network.prefixlen == ip.max_prefixlen:
        return "host_prefix"
    if ip.version == 4 and ip_interface.network.prefixlen < 31 and ip in (ip_interface.network.network_address, ip_interface.network.broadcast_address):
        return "network_or_broadcast_address"
    return None


def infer(snapshot: dict, domain_settings: dict | None = None) -> dict:
    """Infer by (address domain, family, exact prefix, optional manual split).

    Bucket construction is linear; overlap detection sorts prefixes and visits at
    most 128 enclosing prefixes per network, never enumerating hosts or device pairs.
    """
    settings = domain_settings if domain_settings is not None else snapshot.get("domain_settings", {})
    contexts = {item["id"]: item for item in snapshot.get("contexts", [])}
    interfaces = {item["id"]: item for item in snapshot.get("interfaces", [])}
    devices = {item["id"]: item for item in snapshot.get("devices", [])}
    domains = {d["id"]: d for d in settings.get("domains", [])}
    # Membership is configuration evidence, never a physical peer mapping.
    aggregate_members = {member for iface in interfaces.values() for member in iface.get("aggregate_members", [])}
    aggregate_members.update(iface["id"] for iface in interfaces.values() if iface.get("aggregate_interface_id"))

    def is_aggregate_member(interface):
        seen = set()
        while interface and interface["id"] not in seen:
            if interface["id"] in aggregate_members:
                return True
            seen.add(interface["id"])
            interface = interfaces.get(interface.get("parent_interface_id"))
        return False

    buckets = defaultdict(list)
    diagnostics, unmatched, segments, links = [], [], [], []
    assumed = set()

    def unmatch(interface, address, reason, **extra):
        unmatched.append({"id": stable_id("unmatched", interface["id"], address.get("id", address.get("address")), reason),
                          "interface_id": interface["id"], "device_id": interface["device_id"],
                          "address_id": address.get("id"), "address": address.get("address"),
                          "reason": reason, "source_refs": _refs([address, interface]), **extra})

    for interface in sorted(interfaces.values(), key=lambda i: i["id"]):
        domain, implicit = _domain(interface, contexts, settings)
        if interface.get("routing_context_id") and interface["routing_context_id"] not in contexts:
            diagnostics.append(_diagnostic("missing_routing_context", "The routing context is missing; this interface remains isolated until its domain is assigned.", interface_id=interface["id"], context_id=interface["routing_context_id"]))
        if implicit:
            assumed.add(interface.get("routing_context_id") or interface["device_id"])
        member = is_aggregate_member(interface)
        for address in interface.get("addresses", []):
            if member:
                unmatch(interface, address, "aggregate_member")
                continue
            if address.get("address") is None or address.get("prefix_length") is None:
                unmatch(interface, address, "unresolved_address")
                continue
            try:
                ipi = ipaddress.ip_interface(f"{address['address']}/{address['prefix_length']}")
                if address.get("family") not in (None, ipi.version):
                    raise ValueError("Address family disagrees with IP literal")
            except (ValueError, TypeError) as error:
                diagnostics.append(_diagnostic("invalid_address", str(error), interface_id=interface["id"], address_id=address.get("id")))
                unmatch(interface, address, "invalid_address")
                continue
            excluded = _excluded(interface, address, ipi)
            if excluded:
                unmatch(interface, address, excluded)
                continue
            prefix = str(ipi.network)
            bucket_key = f"{domain}|{ipi.version}|{prefix}"
            split = settings.get("segment_splits", {}).get(bucket_key, {}).get(interface["id"], "")
            buckets[(domain, ipi.version, prefix, split)].append((interface, address, ipi))

    if assumed:
        confirmed = domains.get("default", {}).get("confirmed", False)
        item = _diagnostic("default_domain_assumption", "Default routing contexts share the project default address domain; confirm or adjust this assumption.", context_ids=sorted(assumed), confirmed=confirmed)
        item["severity"] = "info" if confirmed else "warning"
        diagnostics.append(item)

    # Duplicate IPs conflict even when their masks differ. Manual split labels
    # provide a separate comparison scope for intentionally reused prefixes.
    global_address_members = defaultdict(set)
    for (domain, family, prefix, split), records in buckets.items():
        for interface, address, ipi in records:
            global_address_members[(domain, family, str(ipi.ip), split)].add(interface["id"])

    pair_links = {}
    segment_networks = defaultdict(list)
    for (domain, family, prefix, split), records in sorted(buckets.items()):
        segment_id = stable_id("segment", domain, family, prefix, split)
        conflicts = []
        address_members = defaultdict(set)
        for interface, address, ipi in records:
            address_members[str(ipi.ip)].add(interface["id"])
        duplicates = {ip: global_address_members[(domain, family, ip, split)] for ip in address_members if len(global_address_members[(domain, family, ip, split)]) > 1}
        for ip, members in duplicates.items():
            conflict = _diagnostic("duplicate_ip", "The same non-virtual IP appears on multiple interfaces; it cannot establish a relation.", address=ip, domain_id=domain, family=family, interface_ids=sorted(members), segment_id=segment_id)
            diagnostics.append(conflict)
            conflicts.append(conflict)
        valid_records = []
        for interface, address, ipi in records:
            if str(ipi.ip) in duplicates:
                unmatch(interface, address, "duplicate_ip", segment_id=segment_id)
            else:
                valid_records.append((interface, address, ipi))
        member_ids = sorted({r[0]["id"] for r in valid_records})
        evidence = {"rule": "exact_prefix", "domain_id": domain, "family": family, "prefix": prefix,
                    "interface_ids": member_ids, "source_refs": _refs([part for r in valid_records for part in r[:2]]),
                    "addresses": [{"interface_id": r[0]["id"], "address": str(r[2].ip)} for r in valid_records],
                    "parser_versions": deepcopy(snapshot.get("parser_versions", {})), "inference_version": VERSION}
        evidence["membership_digest"] = stable_id("membership", domain, family, prefix, member_ids, sorted(evidence["addresses"], key=lambda a: (a["interface_id"], a["address"])))
        partial = sorted({interfaces[i]["device_id"] for i in member_ids if devices.get(interfaces[i]["device_id"], {}).get("parse_status") in ("partial", "failed")})
        if partial:
            warning = _diagnostic("partial_parse_evidence", "This relation includes partially parsed configuration; inspect its source before confirming.", device_ids=partial, segment_id=segment_id)
            conflicts.append(warning)
            diagnostics.append(warning)
        segment = {"id": segment_id, "domain_id": domain, "family": family, "prefix": prefix,
                   "split": split, "bucket_key": f"{domain}|{family}|{prefix}", "interface_ids": member_ids,
                   "evidence": [evidence], "status": "conflict" if duplicates else "pending", "diagnostics": conflicts}
        segments.append(segment)
        segment_networks[(domain, family)].append((ipaddress.ip_network(prefix), segment))
        if len(member_ids) <= 1:
            for interface, address, _ in valid_records:
                unmatch(interface, address, "no_peer", segment_id=segment_id, domain_id=domain, prefix=prefix)
            continue
        if len(member_ids) == 2:
            a, b = (interfaces[i] for i in member_ids)
            if a["device_id"] == b["device_id"]:
                warning = _diagnostic("same_device_prefix", "Two interfaces on the same device share this exact prefix; no device self-loop is inferred.", interface_ids=member_ids, segment_id=segment_id)
                diagnostics.append(warning)
                segment["diagnostics"].append(warning)
                for interface, address, _ in valid_records:
                    unmatch(interface, address, "same_device", segment_id=segment_id)
                continue
            pair = tuple(member_ids)
            if pair not in pair_links:
                pair_links[pair] = {"id": stable_id("link", *pair), "interface_a_id": pair[0], "interface_b_id": pair[1],
                                    "device_a_id": a["device_id"], "device_b_id": b["device_id"],
                                    "relation_type": "ip_inferred", "origin": "inferred", "review_state": "pending",
                                    "families": [], "prefixes": [], "segment_ids": [], "evidence": [], "diagnostics": []}
            link = pair_links[pair]
            for key, item in (("families", family), ("prefixes", prefix), ("segment_ids", segment_id)):
                if item not in link[key]:
                    link[key].append(item)
            link["evidence"].append(evidence)
            link["diagnostics"].extend(conflicts)
        else:
            by_member = defaultdict(list)
            for item in evidence["addresses"]:
                by_member[item["interface_id"]].append(item)
            for interface_id in member_ids:
                local_evidence = _member_evidence({**evidence, "addresses": by_member[interface_id]}, interfaces[interface_id], segment_id)
                links.append({"id": stable_id("member", segment_id, interface_id), "interface_a_id": interface_id,
                              "interface_b_id": None, "device_a_id": interfaces[interface_id]["device_id"], "device_b_id": segment_id,
                              "relation_type": "segment_member", "origin": "inferred", "review_state": "pending",
                              "families": [family], "prefixes": [prefix], "segment_ids": [segment_id],
                              "evidence": [local_evidence], "diagnostics": [_member_diagnostic(d, segment_id, interface_id) for d in conflicts]})

    # CIDR intervals are disjoint or nested. An ancestor stack avoids all-pairs scans.
    overlap_count = 0
    for (domain, family), networks in segment_networks.items():
        stack = []
        # Collapse manual splits of an identical prefix for diagnostic purposes.
        grouped = defaultdict(list)
        for network, segment in networks:
            grouped[network].append(segment)
        for network in sorted(grouped, key=lambda n: (int(n.network_address), n.prefixlen)):
            start = int(network.network_address)
            while stack and int(stack[-1].broadcast_address) < start:
                stack.pop()
            if stack and overlap_count < 2000:
                ancestor = stack[-1]
                involved = grouped[ancestor] + grouped[network]
                warning = _diagnostic("prefix_overlap", "Overlapping prefixes have different masks; exact-prefix inference keeps them separate.", domain_id=domain, family=family, prefixes=[str(ancestor), str(network)], segment_ids=[s["id"] for s in involved])
                diagnostics.append(warning)
                for segment in involved:
                    segment["diagnostics"].append(warning)
                overlap_count += 1
            stack.append(network)
    if overlap_count == 2000:
        diagnostics.append(_diagnostic("overlap_report_limit", "Overlap diagnostics are limited to 2,000 entries; separate address domains to reduce ambiguity."))
    links.extend(pair_links.values())
    # Ensure overlap warnings appear on the related links as well as segments.
    segment_by_id = {s["id"]: s for s in segments}
    for link in links:
        existing = {d["id"] for d in link["diagnostics"]}
        for sid in link["segment_ids"]:
            for diagnostic in segment_by_id[sid]["diagnostics"]:
                if diagnostic["id"] not in existing:
                    link["diagnostics"].append(_member_diagnostic(diagnostic, sid, link["interface_a_id"]) if link["relation_type"] == "segment_member" else diagnostic)
                    existing.add(diagnostic["id"])
    peer_families = defaultdict(lambda: defaultdict(set))
    endpoint_links = defaultdict(list)
    for link in links:
        if link["relation_type"] == "ip_inferred":
            for endpoint, peer in ((link["interface_a_id"], link["interface_b_id"]), (link["interface_b_id"], link["interface_a_id"])):
                endpoint_links[endpoint].append(link)
                for family in link["families"]:
                    peer_families[endpoint][family].add(peer)
    for endpoint, families in peer_families.items():
        if families[4] and families[6] and families[4] != families[6]:
            warning = _diagnostic("dual_stack_peer_mismatch", "IPv4 and IPv6 identify different interface peers; their relations remain separate.", interface_id=endpoint, ipv4_peers=sorted(families[4]), ipv6_peers=sorted(families[6]))
            diagnostics.append(warning)
            for link in endpoint_links[endpoint]:
                link["diagnostics"].append(warning)
    return {"segments": segments, "links": sorted(links, key=lambda l: l["id"]), "unmatched": unmatched, "diagnostics": diagnostics}


def _without_text(value, in_source_refs=False):
    if isinstance(value, dict):
        return {k: _without_text(v, in_source_refs or k == "source_refs") for k, v in value.items() if not (k == "text" and in_source_refs)}
    if isinstance(value, list):
        return [_without_text(v, in_source_refs) for v in value]
    return value


def project_graph(snapshot: dict, mode="device") -> dict:
    """Project the reviewed model without fabricating a clique for shared prefixes."""
    if mode not in ("device", "segment"):
        raise ValueError("mode must be device or segment")
    interfaces = {i["id"]: i for i in snapshot.get("interfaces", [])}
    segments = {s["id"]: s for s in snapshot.get("segments", [])}
    nodes = [{**deepcopy(d), "label": d.get("hostname") or d["id"], "kind": "device"} for d in snapshot.get("devices", [])]
    visible_segments, edges = set(), []

    def edge(link, source, target, **extra):
        a = interfaces.get(link.get("interface_a_id"), {})
        b = interfaces.get(link.get("interface_b_id"), {})
        label = " ↔ ".join(x for x in (a.get("normalized_name", a.get("raw_name", "")), b.get("normalized_name", b.get("raw_name", ""))) if x)
        result = {**deepcopy(link), "source": source, "target": target, "label": label, **extra}
        def short_name(interface_id):
            value = interfaces.get(interface_id, {}).get("raw_name", "")
            for full, short in (("TenGigabitEthernet", "Te"), ("GigabitEthernet", "Gi"), ("FastEthernet", "Fa"), ("Bundle-Ether", "BE"), ("Port-channel", "Po")):
                if value.startswith(full):
                    return short + value[len(full):]
            return value
        result["source_label"] = short_name(result.get("interface_a_id"))
        result["target_label"] = short_name(result.get("interface_b_id"))
        edges.append(result)

    for link in snapshot.get("links", []):
        if link.get("review_state") == "rejected":
            continue
        if link.get("relation_type") == "segment_member":
            visible_segments.add(link["device_b_id"])
            edge(link, link["device_a_id"], link["device_b_id"])
        elif mode == "segment" and link.get("origin") == "inferred" and link.get("segment_ids"):
            for sid in link["segment_ids"]:
                if sid not in segments:
                    continue
                visible_segments.add(sid)
                for side in ("a", "b"):
                    iid = link[f"interface_{side}_id"]
                    edge(link, link[f"device_{side}_id"], sid, id=stable_id("projection", link["id"], sid, iid), link_id=link["id"], interface_a_id=iid, interface_b_id=None, device_a_id=link[f"device_{side}_id"], device_b_id=sid, relation_type="segment_member", families=[segments[sid]["family"]], prefixes=[segments[sid]["prefix"]], segment_ids=[sid], evidence=[_member_evidence(e, interfaces[iid], sid) for e in segments[sid]["evidence"]], label=interfaces.get(iid, {}).get("raw_name", iid))
        else:
            edge(link, link["device_a_id"], link["device_b_id"])
    for sid in sorted(visible_segments):
        if sid in segments:
            segment = deepcopy(segments[sid])
            nodes.append({**segment, "kind": "segment", "label": segment["prefix"]})
    return _without_text({"nodes": nodes, "edges": edges})
