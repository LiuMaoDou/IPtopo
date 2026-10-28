"""Auditable override replay on immutable configuration facts.

Call with the raw snapshot, and persist both returned values. Evaluated records
capture baselines on creation; later replay checks those baselines before edits.
"""
from __future__ import annotations

from copy import deepcopy
import ipaddress
import json
from .inference import infer, stable_id, _refs

FACT_OPERATIONS = {"add_device", "add_interface", "override_field", "assign_domain", "split_segment"}
RELATION_OPERATIONS = {"force_link", "confirm_link", "suppress_link", "rebind_endpoint"}
ALLOWED_FIELDS = {
    "device": {"hostname", "vendor", "os", "site", "role", "description"},
    "interface": {"raw_name", "normalized_name", "kind", "description", "admin_state", "effective_state", "routing_context_id", "encapsulation"},
    "address": {"address", "prefix_length", "role", "effective_state", "acquisition_method"},
}


class OverrideConflict(ValueError):
    pass


def _entities(snapshot, kind):
    if kind == "address":
        return [address for interface in snapshot.get("interfaces", []) for address in interface.get("addresses", [])]
    return snapshot.get({"device": "devices", "interface": "interfaces", "link": "links", "segment": "segments"}.get(kind, ""), [])


def _entity(snapshot, kind, entity_id):
    candidates = [item for item in _entities(snapshot, kind) if item["id"] == entity_id]
    if len(candidates) != 1:
        raise OverrideConflict(f"{kind} target is missing or ambiguous: {entity_id}")
    return candidates[0]


def _target_entity(snapshot, target):
    kind = target.get("kind", target.get("entity_type", target.get("type")))
    entity_id = target.get("id", target.get("entity_id"))
    if not kind:
        for candidate in ("device", "interface", "address", "link", "segment"):
            if target.get(f"{candidate}_id"):
                kind, entity_id = candidate, target[f"{candidate}_id"]
                break
    return kind, _entity(snapshot, kind, entity_id)


def relation_premise(link, *, legacy=False):
    """Source line movement is harmless; domain, addresses and membership are not."""
    evidence = []
    for entry in link.get("evidence", []):
        item = {"rule": entry.get("rule"), "domain_id": entry.get("domain_id"),
                "family": entry.get("family"), "prefix": entry.get("prefix"),
                "interface_ids": sorted(entry.get("interface_ids", [])),
                "addresses": sorted(entry.get("addresses", []), key=lambda a: (a.get("interface_id", ""), a.get("address", "")))}
        if not legacy:
            item["membership_digest"] = entry.get("membership_digest")
        evidence.append(item)
    return stable_id("premise", link.get("relation_type"), sorted(filter(None, (link.get("interface_a_id"), link.get("interface_b_id")))), sorted(evidence, key=lambda e: json.dumps(e, sort_keys=True)))


def _legacy_relation_premise(snapshot, link):
    """Compare old saved reviews against complete current evidence before upgrading."""
    if link.get("relation_type") == "segment_member":
        # Before compact spokes, each member stored the entire segment evidence.
        # Comparing only its own address would miss changes to other members.
        segments = {segment["id"]: segment for segment in snapshot.get("segments", [])}
        segment_ids = link.get("segment_ids", [])
        if not segment_ids or any(segment_id not in segments for segment_id in segment_ids):
            return None
        link = {**link, "evidence": [entry for segment_id in segment_ids for entry in segments[segment_id].get("evidence", [])]}
    return relation_premise(link, legacy=True)


def _link(snapshot, target):
    if target.get("link_id") or target.get("id"):
        return _entity(snapshot, "link", target.get("link_id", target.get("id")))
    endpoints = {target.get("interface_a_id"), target.get("interface_b_id")}
    matches = [link for link in snapshot.get("links", []) if {link.get("interface_a_id"), link.get("interface_b_id")} == endpoints and link.get("relation_type") == target.get("relation_type", "ip_inferred")]
    if len(matches) != 1:
        raise OverrideConflict("The relation is missing or its endpoint identity is ambiguous")
    return matches[0]


def _guard_relation(snapshot, record, link):
    baseline = record.setdefault("baseline", {})
    premise = relation_premise(link)
    if not baseline.get("premise"):
        if record.get("base_snapshot_id") and record["base_snapshot_id"] != snapshot.get("id"):
            raise OverrideConflict("This old relation edit has no recorded baseline; review its current evidence")
        baseline["premise"] = premise
        baseline["relation"] = {key: deepcopy(link.get(key)) for key in ("id", "interface_a_id", "interface_b_id", "prefixes", "segment_ids", "evidence")}
    elif baseline["premise"] != premise:
        if baseline["premise"] != _legacy_relation_premise(snapshot, link):
            raise OverrideConflict("The relation's address, domain, prefix, or member evidence changed")
        # Exact old-hash equality proves unchanged evidence. Upgrade the saved
        # baseline so all subsequent replays use the compact membership digest.
        baseline["premise"] = premise
        baseline["relation"] = {key: deepcopy(link.get(key)) for key in ("id", "interface_a_id", "interface_b_id", "prefixes", "segment_ids", "evidence")}


def _guard_value(record, old, desired):
    baseline = record.setdefault("baseline", {})
    if "old_value" not in baseline:
        baseline["old_value"] = deepcopy(baseline.get("value", old))
    if baseline["old_value"] != old and old != desired:
        record["current_value"] = deepcopy(old)
        raise OverrideConflict("The configuration field changed since this override was created")


def _normalize_address(address, interface_id, index):
    result = {"role": "primary", "acquisition_method": "static", "effective_state": "active", "source_refs": [], **deepcopy(address)}
    result["id"] = result.get("id") or stable_id("address", interface_id, index)
    result["interface_id"] = interface_id
    if result.get("address") is not None:
        try:
            ipi = ipaddress.ip_interface(f"{result['address']}/{result['prefix_length']}")
        except (ValueError, KeyError) as error:
            raise OverrideConflict(f"Invalid manual address: {error}") from error
        result.update(address=str(ipi.ip), family=ipi.version, prefix_length=ipi.network.prefixlen, canonical_network=str(ipi.network))
    return result


def _manual_link(snapshot, record, a_id, b_id):
    a, b = _entity(snapshot, "interface", a_id), _entity(snapshot, "interface", b_id)
    if a_id == b_id:
        raise OverrideConflict("A manual relation needs two different interfaces")
    a_id, b_id = sorted((a_id, b_id))
    a, b = _entity(snapshot, "interface", a_id), _entity(snapshot, "interface", b_id)
    return {"id": stable_id("manual-link", record["id"], a_id, b_id), "interface_a_id": a_id, "interface_b_id": b_id,
            "device_a_id": a["device_id"], "device_b_id": b["device_id"], "relation_type": "manual",
            "origin": "manual", "review_state": "confirmed", "families": sorted(
                {address.get("family") for address in a.get("addresses", []) if address.get("address") and address.get("family") in (4, 6)}
                & {address.get("family") for address in b.get("addresses", []) if address.get("address") and address.get("family") in (4, 6)}
            ), "prefixes": [], "segment_ids": [],
            "evidence": [{"rule": "manual_relation", "domain_id": None, "family": None, "prefix": None,
                          "interface_ids": [a_id, b_id], "source_refs": _refs([a, b]), "override_id": record["id"], "reason": record.get("reason", "")}],
            "diagnostics": [], "override_id": record["id"]}


def _fact(snapshot, settings, record):
    op, target, value = record["operation"], record.get("target", {}), record.get("value", {})
    if op == "add_device":
        device_id = value.get("id") or stable_id("manual-device", record["id"])
        if any(d["id"] == device_id for d in snapshot.get("devices", [])):
            raise OverrideConflict("The manual device identity already exists")
        if not value.get("hostname", "").strip():
            raise OverrideConflict("A manual device requires a hostname")
        snapshot.setdefault("devices", []).append({"vendor": "manual", "os": "manual", "format": "manual", "site": "", "source_file_id": None,
            "parse_status": "manual", "source_refs": [], **deepcopy(value), "id": device_id, "identity_key": f"manual:{record['id']}"})
        record["result_id"] = device_id
    elif op == "add_interface":
        device_id = value.get("device_id", target.get("device_id"))
        _entity(snapshot, "device", device_id)
        interface_id = value.get("id") or stable_id("manual-interface", record["id"])
        if any(i["id"] == interface_id for i in snapshot.get("interfaces", [])):
            raise OverrideConflict("The manual interface identity already exists")
        name = value.get("raw_name", value.get("name", "")).strip()
        if not name:
            raise OverrideConflict("A manual interface requires a name")
        context_id = value.get("routing_context_id")
        if context_id:
            context = next((c for c in snapshot.get("contexts", []) if c["id"] == context_id and c["device_id"] == device_id), None)
            if not context:
                raise OverrideConflict("The routing context must belong to the interface's device")
        else:
            context = next((c for c in snapshot.get("contexts", []) if c["device_id"] == device_id and c.get("kind") == "default"), None)
            context_id = context["id"] if context else stable_id("manual-context", device_id, "default")
            if context is None:
                snapshot.setdefault("contexts", []).append({"id": context_id, "device_id": device_id, "local_name": "default", "kind": "default", "rd": None, "rt": [], "source_refs": []})
        addresses = [_normalize_address(a, interface_id, index) for index, a in enumerate(value.get("addresses", []))]
        snapshot.setdefault("interfaces", []).append({"kind": "physical", "parent_interface_id": None, "physical_port_refs": [], "aggregate_members": [], "encapsulation": None,
            "description": "", "admin_state": "enabled", "oper_state": "unknown", "effective_state": "active", "source_refs": [], **deepcopy(value),
            "id": interface_id, "device_id": device_id, "raw_name": name, "normalized_name": value.get("normalized_name", name), "routing_context_id": context_id, "addresses": addresses})
        record["result_id"] = interface_id
    elif op == "override_field":
        kind, entity = _target_entity(snapshot, target)
        field = target.get("field", value.get("field"))
        if field not in ALLOWED_FIELDS.get(kind, set()):
            raise OverrideConflict("That field cannot be overridden; use a dedicated identity or relation operation")
        desired = value.get("value")
        if field == "prefix_length":
            if not isinstance(desired, int) or isinstance(desired, bool):
                raise OverrideConflict("Prefix length must be an integer")
        elif field == "encapsulation":
            if desired is not None and not isinstance(desired, dict):
                raise OverrideConflict("Encapsulation must be an object or null")
        elif not isinstance(desired, str):
            raise OverrideConflict("This field requires a string value")
        allowed_states = {"admin_state": {"enabled", "disabled", "unknown"}, "effective_state": {"active", "inactive", "unresolved"},
                          "role": {"primary", "secondary", "virtual", "anycast", "link_local", "unknown"} if kind == "address" else None,
                          "acquisition_method": {"static", "dhcp", "slaac", "unnumbered", "derived", "unresolved"}}
        if allowed_states.get(field) and desired not in allowed_states[field]:
            raise OverrideConflict("The requested state or address role is not recognized")
        _guard_value(record, entity.get(field), desired)
        candidate = deepcopy(entity)
        candidate[field] = deepcopy(desired)
        if kind == "interface" and field == "raw_name":
            candidate["normalized_name"] = desired
        if kind == "address":
            candidate = _normalize_address(candidate, candidate["interface_id"], 0)
        if kind == "interface" and field == "routing_context_id":
            if not any(c["id"] == desired and c["device_id"] == entity["device_id"] for c in snapshot.get("contexts", [])):
                raise OverrideConflict("The routing context is missing or belongs to another device")
        entity.update(candidate)
        entity.setdefault("overridden_fields", {})[field] = {"override_id": record["id"], "original": record["baseline"]["old_value"]}
    elif op == "assign_domain":
        domain_id = value.get("domain_id")
        if not isinstance(domain_id, str) or not domain_id.strip():
            raise OverrideConflict("A domain id is required")
        interface_id = target.get("interface_id")
        context_id = target.get("context_id", target.get("routing_context_id"))
        if interface_id:
            _entity(snapshot, "interface", interface_id)
            collection, entity_id = "interface_mappings", interface_id
        elif context_id and any(c["id"] == context_id for c in snapshot.get("contexts", [])):
            collection, entity_id = "mappings", context_id
        else:
            raise OverrideConflict("The domain mapping target is missing")
        mappings = settings.setdefault(collection, {})
        _guard_value(record, mappings.get(entity_id), domain_id)
        mappings[entity_id] = domain_id
        existing = next((d for d in settings.setdefault("domains", []) if d["id"] == domain_id), None)
        if existing is None:
            settings["domains"].append({"id": domain_id, "name": value.get("name", domain_id), "confirmed": value.get("confirmed", False)})
        elif "confirmed" in value:
            existing["confirmed"] = bool(value["confirmed"])
    elif op == "split_segment":
        if target.get("segment_id"):
            segment = _entity(snapshot, "segment", target["segment_id"])
            bucket_key = segment["bucket_key"]
        else:
            bucket_key = target.get("bucket_key")
        candidates = [s for s in snapshot.get("segments", []) if s.get("bucket_key") == bucket_key]
        if not candidates:
            raise OverrideConflict("The prefix bucket no longer exists")
        assignments = value.get("assignments", {})
        members = {iid for s in candidates for iid in s["interface_ids"]}
        if not assignments or not set(assignments).issubset(members) or not all(isinstance(v, str) for v in assignments.values()):
            raise OverrideConflict("Split assignments must name current bucket interfaces and string split names")
        splits = settings.setdefault("segment_splits", {})
        existing = splits.get(bucket_key, {})
        previous = {iid: existing[iid] for iid in assignments if iid in existing}
        _guard_value(record, previous, assignments)
        splits[bucket_key] = {**existing, **deepcopy(assignments)}


def _relation(snapshot, record):
    op, target, value = record["operation"], record.get("target", {}), record.get("value", {})
    if op == "force_link":
        link = _manual_link(snapshot, record, value.get("interface_a_id", target.get("interface_a_id")), value.get("interface_b_id", target.get("interface_b_id")))
        snapshot["links"].append(link)
        record["result_id"] = link["id"]
        return
    link = _link(snapshot, target)
    _guard_relation(snapshot, record, link)
    if op in ("suppress_link", "confirm_link"):
        link["review_state"] = "rejected" if op == "suppress_link" else "confirmed"
        link["review_override_id"] = record["id"]
        link["review_reason"] = record.get("reason", "")
    elif op == "rebind_endpoint":
        side = value.get("endpoint", "b")
        if side not in ("a", "b"):
            raise OverrideConflict("endpoint must be a or b")
        if link.get("relation_type") == "segment_member":
            raise OverrideConflict("Split a shared prefix or create an explicit manual relation to change its members")
        a_id = value.get("interface_a_id", link["interface_a_id"])
        b_id = value.get("interface_b_id", link["interface_b_id"])
        if "interface_id" in value:
            if side == "a":
                a_id = value["interface_id"]
            else:
                b_id = value["interface_id"]
        replacement = _manual_link(snapshot, record, a_id, b_id)
        replacement["replaces_link_id"] = link["id"]
        link["review_state"] = "rejected"
        link["review_override_id"] = record["id"]
        snapshot["links"].append(replacement)
        record["result_id"] = replacement["id"]


def _note(snapshot, record):
    target = record.get("target", {})
    text = record.get("value", {}).get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise OverrideConflict("A note must contain text")
    if target.get("kind") == "project" or not target:
        entity = snapshot
    else:
        _, entity = _target_entity(snapshot, target)
    entity.setdefault("notes", []).append({"id": stable_id("note", record["id"]), "text": text, "override_id": record["id"]})


def apply_overrides(snapshot: dict, overrides: list, domain_settings: dict | None = None) -> tuple[dict, list]:
    result = deepcopy(snapshot)
    settings = deepcopy(domain_settings if domain_settings is not None else result.get("domain_settings", {}))
    records = deepcopy(overrides)
    parser_diagnostics = [d for d in result.get("diagnostics", []) if d.get("stage") not in ("inference", "override")]

    def recompute():
        inferred = infer(result, settings)
        result.update(inferred)
        result["diagnostics"] = deepcopy(parser_diagnostics) + inferred["diagnostics"]

    recompute()
    for index, record in enumerate(records):
        record.setdefault("id", stable_id("override", index, record))
        record.setdefault("status", "active")
        record.setdefault("base_snapshot_id", snapshot.get("id"))
        record.pop("conflict", None)
        record.pop("current_value", None)

    def evaluate(record, action):
        if record["status"] == "undone":
            return
        try:
            action()
            record["status"] = "active"
        except (OverrideConflict, KeyError, TypeError, ValueError) as error:
            record["status"] = "conflict"
            record["conflict"] = str(error)

    def descriptive_edit(record):
        if record.get("operation") != "override_field":
            return False
        target, value = record.get("target", {}), record.get("value", {})
        kind = target.get("kind", target.get("entity_type", target.get("type")))
        if not kind:
            kind = next((k for k in ("device", "interface", "address") if target.get(f"{k}_id")), None)
        field = target.get("field", value.get("field"))
        return (kind == "device" and field in ALLOWED_FIELDS["device"]) or (kind == "interface" and field in {"description", "raw_name", "normalized_name"})

    # Apply factual corrections first, then review their resulting relationships.
    # Re-evaluate earlier records on every import; an undone record is never replayed.
    for record in records:
        if record.get("operation") in FACT_OPERATIONS:
            if descriptive_edit(record):
                # _fact validates a separate entity candidate before updating it.
                # Descriptive fields cannot change domain/prefix membership, so
                # neither a whole-project rollback copy nor reinference is needed.
                evaluate(record, lambda r=record: _fact(result, settings, r))
                continue
            # Mutation is transactional per record; failures never leave half an edit.
            before, settings_before = deepcopy(result), deepcopy(settings)
            evaluate(record, lambda r=record: _fact(result, settings, r))
            if record["status"] == "conflict":
                result, settings = before, settings_before
            elif record["status"] != "undone":
                recompute()
    for record in records:
        if record.get("operation") in RELATION_OPERATIONS:
            evaluate(record, lambda r=record: _relation(result, r))
    for record in records:
        if record.get("operation") == "add_note":
            evaluate(record, lambda r=record: _note(result, r))
        elif record.get("operation") not in FACT_OPERATIONS | RELATION_OPERATIONS:
            evaluate(record, lambda: (_ for _ in ()).throw(OverrideConflict("Unsupported override operation")))
    for record in records:
        if record["status"] == "conflict":
            result["diagnostics"].append({"id": stable_id("override-conflict", record["id"]), "severity": "warning", "code": "override_conflict", "stage": "override", "message": record["conflict"], "details": {"override_id": record["id"], "operation": record.get("operation"), "target": record.get("target", {}), "baseline": record.get("baseline", {}), "current_value": record.get("current_value")}})
    result["domain_settings"] = settings
    return result, records
