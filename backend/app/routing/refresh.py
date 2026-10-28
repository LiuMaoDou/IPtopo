"""Refresh derived routing facts from retained immutable configuration sources.

Does not write projects, replace snapshots, or alter imported interface facts.
Missing sources and mismatched IDs retain their original conservative diagnostics.
"""
from copy import deepcopy

from ..parser import parse_config
from .adapters.huawei import VERSION
from .adapters.auto_cost import automatic_cost, bandwidth_resolver


def refresh_huawei_routing(snapshot, read_source):
    old = snapshot.get("routing") or {}
    if old.get("adapter_versions", {}).get("huawei_vrp_igp") == VERSION:
        return snapshot
    devices = [d for d in snapshot.get("devices", []) if d.get("vendor") == "Huawei"]
    if not devices:
        return snapshot
    sources = {s["id"]: s for s in snapshot.get("source_files", [])}
    interfaces = {i["id"]: i for i in snapshot.get("interfaces", [])}
    contexts = {c["id"] for c in snapshot.get("contexts", [])}
    updated = deepcopy(old)
    source_updates = {}
    bandwidth_metadata = {}
    refreshed = 0
    for device in devices:
        source_id = device.get("source_file_id")
        source = sources.get(source_id)
        if not source:
            continue
        text = read_source(source_id)
        if text is None:
            continue
        parsed = parse_config(text, source["filename"], source_id, device["id"], dialect="huawei_vrp")
        facts = parsed.get("routing", {})
        if parsed.get("status") == "failed" or any(b["interface_id"] not in interfaces for b in facts.get("bindings", [])):
            continue
        if any(i["routing_context_id"] not in contexts for i in facts.get("instances", [])):
            continue
        if any(a["address_id"] not in {v["id"] for v in interfaces[a["interface_id"]].get("addresses", [])}
               for a in facts.get("advertisements", [])):
            continue
        for key in ("instances", "bindings", "advertisements", "issues"):
            updated[key] = [row for row in updated.get(key, []) if row["device_id"] != device["id"]] + facts.get(key, [])
        updated["adapter_devices"] = sorted(set(updated.get("adapter_devices", [])) | {device["id"]})
        source_updates[source_id] = {**source, "diagnostics": parsed["diagnostics"], "parse_status": parsed["status"]}
        bandwidth_metadata.update({i["id"]: i["configured_bandwidth_mbps"] for i in parsed["interfaces"] if i.get("configured_bandwidth_mbps")})
        refreshed += 1
    if not refreshed:
        return snapshot
    updated.setdefault("schema_version", 1)
    if refreshed == len(devices):
        updated.setdefault("adapter_versions", {})["huawei_vrp_igp"] = VERSION
    return {**snapshot, "routing": updated,
            "interfaces": [{**i, "configured_bandwidth_mbps": bandwidth_metadata[i["id"]]} if i["id"] in bandwidth_metadata else i for i in snapshot.get("interfaces", [])],
            "source_files": [source_updates.get(s["id"], s) for s in snapshot.get("source_files", [])]}


def apply_bandwidth_assumptions(snapshot):
    """Recompute only devices with explicit, auditable bandwidth assumptions."""
    devices = {i["device_id"] for i in snapshot.get("interfaces", []) if i.get("assumed_bandwidth_mbps")}
    if not devices:
        return snapshot
    resolvers = {device: bandwidth_resolver([i for i in snapshot["interfaces"] if i["device_id"] == device], {}) for device in devices}
    interfaces = {i["id"]: i for i in snapshot["interfaces"]}
    for binding in snapshot.get("routing", {}).get("bindings", []):
        mode = binding.get("automatic_cost")
        if binding["device_id"] not in resolvers or not mode:
            continue
        value, notes = resolvers[binding["device_id"]](interfaces[binding["interface_id"]])
        cost = automatic_cost(value, mode["reference_bandwidth_mbps"], mode["style"], mode.get("compatible", False))
        if cost is not None and cost > binding["metric_limit"]:
            cost = None
        binding.update(cost=cost, cost_origin="bandwidth_derived" if cost is not None else "unknown",
                       bandwidth_mbps=float(value) if value else None, cost_assumptions=notes +
                       (["自动开销按配置公式计算，取整数且最低为 1。"] if cost is not None else []))
    return snapshot
