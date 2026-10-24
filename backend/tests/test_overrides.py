from copy import deepcopy

import pytest

from backend.app.inference import infer, project_graph
from backend.app.overrides import apply_overrides
from backend.tests.test_inference import topology


def override(operation, target=None, value=None, **extra):
    return {"id": f"edit-{operation}", "operation": operation, "target": target or {}, "value": value or {}, "reason": "operator review", "base_snapshot_id": "snapshot-1", "status": "active", **extra}


def link_target(snapshot):
    return {"link_id": infer(snapshot)["links"][0]["id"]}


def test_at_15_suppression_replays_and_undo_restores_relation():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    before = deepcopy(snapshot)
    result, records = apply_overrides(snapshot, [override("suppress_link", link_target(snapshot))])
    assert snapshot == before
    assert result["links"][0]["review_state"] == "rejected"
    assert not project_graph(result)["edges"]
    assert records[0]["baseline"]["premise"]
    snapshot["id"] = "snapshot-2"
    for interface in snapshot["interfaces"]:
        interface["addresses"][0]["source_refs"][0]["line"] += 100
    replay, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "active"
    assert replay["links"][0]["review_state"] == "rejected"
    records[0]["status"] = "undone"
    restored, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "undone"
    assert restored["links"][0]["review_state"] == "pending"


def test_suppression_does_not_reject_changed_prefix_with_same_endpoint_ids():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    _, records = apply_overrides(snapshot, [override("suppress_link", link_target(snapshot))])
    changed = topology("10.0.1.1/30", "10.0.1.2/30")
    changed["id"] = "snapshot-2"
    result, records = apply_overrides(changed, records)
    assert result["links"][0]["review_state"] == "pending"
    assert records[0]["status"] == "conflict"
    assert any(d["code"] == "override_conflict" for d in result["diagnostics"])


def test_suppression_does_not_reject_changed_address_with_same_prefix():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24")
    _, records = apply_overrides(snapshot, [override("suppress_link", link_target(snapshot))])
    changed = topology("10.0.0.3/24", "10.0.0.2/24")
    changed["id"] = "snapshot-2"
    result, records = apply_overrides(changed, records)
    assert result["links"][0]["review_state"] == "pending"
    assert records[0]["status"] == "conflict"


def test_suppression_without_historical_baseline_is_conflict():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    target = link_target(snapshot)
    snapshot["id"] = "snapshot-2"
    result, records = apply_overrides(snapshot, [override("suppress_link", target)])
    assert result["links"][0]["review_state"] == "pending"
    assert records[0]["status"] == "conflict"


def test_confirm_is_explicit_and_origin_remains_inferred():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    result, records = apply_overrides(snapshot, [override("confirm_link", link_target(snapshot))])
    assert result["links"][0]["origin"] == "inferred"
    assert result["links"][0]["review_state"] == "confirmed"
    assert records[0]["status"] == "active"


def test_at_16_rebind_missing_endpoint_conflicts_and_recovers():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30", "10.1.0.1/30")
    result, records = apply_overrides(snapshot, [override("rebind_endpoint", link_target(snapshot), {"endpoint": "b", "interface_id": "i2"})])
    assert len(result["links"]) == 2
    inferred = next(link for link in result["links"] if link["origin"] == "inferred")
    manual = next(link for link in result["links"] if link["origin"] == "manual")
    assert inferred["review_state"] == "rejected"
    assert {manual["interface_a_id"], manual["interface_b_id"]} == {"i0", "i2"}
    assert manual["evidence"][0]["rule"] == "manual_relation"
    changed = deepcopy(snapshot)
    changed["id"] = "snapshot-2"
    changed["interfaces"] = changed["interfaces"][:2]
    result, records = apply_overrides(changed, records)
    assert records[0]["status"] == "conflict"
    assert len(result["links"]) == 1
    assert result["links"][0]["review_state"] == "pending"
    result, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "active"
    assert len(result["links"]) == 2


def test_field_override_keeps_original_and_detects_reimport_conflict():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    record = override("override_field", {"kind": "device", "id": "d0", "field": "hostname"}, {"value": "Paris-PE"})
    result, records = apply_overrides(snapshot, [record])
    assert result["devices"][0]["hostname"] == "Paris-PE"
    assert result["devices"][0]["overridden_fields"]["hostname"]["original"] == "router-0"
    assert snapshot["devices"][0]["hostname"] == "router-0"
    snapshot["devices"][0]["hostname"] = "router-renamed"
    snapshot["id"] = "snapshot-2"
    result, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "conflict"
    assert records[0]["current_value"] == "router-renamed"
    assert result["devices"][0]["hostname"] == "router-renamed"


def test_explicit_field_baseline_prevents_stale_edit():
    snapshot = topology("10.0.0.1/30")
    record = override("override_field", {"kind": "device", "id": "d0", "field": "hostname"}, {"value": "Paris-PE"}, baseline={"value": "outdated"})
    result, records = apply_overrides(snapshot, [record])
    assert result["devices"][0]["hostname"] == "router-0"
    assert records[0]["status"] == "conflict"


def test_address_edit_recomputes_prefix_and_invalid_edit_rolls_back():
    snapshot = topology("10.0.0.1/30", "10.0.1.2/30")
    record = override("override_field", {"kind": "address", "id": "a1-0", "field": "address"}, {"value": "10.0.0.2"})
    result, records = apply_overrides(snapshot, [record])
    assert len(result["links"]) == 1
    assert result["interfaces"][1]["addresses"][0]["canonical_network"] == "10.0.0.0/30"
    record["value"]["value"] = "not-an-ip"
    result, records = apply_overrides(snapshot, [record])
    assert records[0]["status"] == "conflict"
    assert result["interfaces"][1]["addresses"][0]["address"] == "10.0.1.2"
    assert not result["links"]


def test_force_link_requires_exact_existing_interface_and_is_manual():
    snapshot = topology("10.0.0.1/30", "10.1.0.2/30")
    record = override("force_link", value={"interface_a_id": "i0", "interface_b_id": "i1"})
    result, records = apply_overrides(snapshot, [record])
    assert len(result["links"]) == 1
    assert result["links"][0]["origin"] == "manual"
    assert result["links"][0]["review_state"] == "confirmed"
    snapshot["interfaces"].pop()
    result, records = apply_overrides(snapshot, records)
    assert not result["links"]
    assert records[0]["status"] == "conflict"


def test_manual_device_interface_and_link_can_be_composed_and_replayed():
    snapshot = topology("10.0.0.1/30")
    records = [override("add_device", value={"id": "manual-router", "hostname": "missing-CE"}),
               override("add_interface", value={"id": "manual-port", "device_id": "manual-router", "raw_name": "GE0", "addresses": [{"address": "10.0.0.2", "prefix_length": 30}]}),
               override("add_note", {"kind": "device", "id": "manual-router"}, {"text": "Configuration not yet available"})]
    result, evaluated = apply_overrides(snapshot, records)
    assert all(r["status"] == "active" for r in evaluated)
    assert len(result["devices"]) == 2
    assert len(result["links"]) == 1
    assert result["devices"][1]["notes"][0]["text"] == "Configuration not yet available"
    assert len(snapshot["devices"]) == 1
    replay, _ = apply_overrides(snapshot, evaluated)
    assert replay == result


def test_manual_add_failed_address_does_not_leave_partial_context():
    snapshot = topology("10.0.0.1/30")
    snapshot["contexts"] = []
    record = override("add_interface", value={"device_id": "d0", "raw_name": "bad-interface", "addresses": [{"address": "invalid", "prefix_length": 24}]})
    result, records = apply_overrides(snapshot, [record])
    assert records[0]["status"] == "conflict"
    assert not result["contexts"]
    assert len(result["interfaces"]) == 1


def test_domain_mapping_recomputes_and_undo_uses_base_domain_settings():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    snapshot["contexts"][0].update(kind="vrf", local_name="customer")
    records = [override("assign_domain", {"context_id": "c0"}, {"domain_id": "default", "confirmed": True})]
    result, records = apply_overrides(snapshot, records)
    assert len(result["links"]) == 1
    assert result["domain_settings"]["mappings"]["c0"] == "default"
    records[0]["status"] = "undone"
    result, records = apply_overrides(snapshot, records)
    assert len(result["links"]) == 1
    assert not result["domain_settings"].get("mappings")


def test_split_segment_produces_two_exact_endpoint_pairs():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24", "10.0.0.4/24")
    sid = infer(snapshot)["segments"][0]["id"]
    record = override("split_segment", {"segment_id": sid}, {"assignments": {"i0": "a", "i1": "a", "i2": "b", "i3": "b"}})
    result, records = apply_overrides(snapshot, [record])
    assert records[0]["status"] == "active"
    assert len(result["links"]) == 2
    assert len(result["segments"]) == 2
    assert all(l["relation_type"] == "ip_inferred" for l in result["links"])
    replay, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "active"
    assert replay["links"] == result["links"]


def test_fact_edit_invalidates_previous_confirm_when_relation_premise_changes():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24")
    _, records = apply_overrides(snapshot, [override("confirm_link", link_target(snapshot))])
    records.append(override("override_field", {"kind": "address", "id": "a0-0", "field": "address"}, {"value": "10.0.0.3"}))
    result, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "conflict"
    assert records[1]["status"] == "active"
    assert result["links"][0]["review_state"] == "pending"


def test_rejected_shared_segment_member_stays_rejected_in_both_projections():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24")
    result, _ = apply_overrides(snapshot, [override("suppress_link", link_target(snapshot))])
    for mode in ("device", "segment"):
        graph = project_graph(result, mode)
        assert len(graph["edges"]) == 2
        assert len({e["target"] for e in graph["edges"]}) == 1


def test_unknown_operation_and_identity_edits_report_conflict():
    snapshot = topology("10.0.0.1/30")
    records = [override("unknown"), override("override_field", {"kind": "device", "id": "d0", "field": "id"}, {"value": "other-id"})]
    result, records = apply_overrides(snapshot, records)
    assert all(r["status"] == "conflict" for r in records)
    assert result["devices"][0]["id"] == "d0"


def test_splitting_one_group_preserves_other_previously_separated_groups():
    snapshot = topology(*[f"10.0.0.{i}/24" for i in range(1, 9)])
    first = override("split_segment", {"bucket_key": "default|4|10.0.0.0/24"}, {"assignments": {"i0": "a", "i1": "a", "i2": "a", "i3": "a", "i4": "b", "i5": "b", "i6": "c", "i7": "c"}})
    initial, records = apply_overrides(snapshot, [first])
    group_a = next(s for s in initial["segments"] if s["split"] == "a")
    second = override("split_segment", {"segment_id": group_a["id"]}, {"assignments": {"i0": "a1", "i1": "a1", "i2": "a2", "i3": "a2"}}, id="second-split")
    result, records = apply_overrides(snapshot, records + [second])
    assert all(r["status"] == "active" for r in records)
    assert len(result["links"]) == 4
    assert {s["split"] for s in result["segments"]} == {"a1", "a2", "b", "c"}


def test_interface_display_rename_updates_label_preserving_endpoint_identity():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    snapshot["interfaces"][0]["normalized_name"] = "GigabitEthernet0/0"
    result, records = apply_overrides(snapshot, [override("override_field", {"kind": "interface", "id": "i0", "field": "raw_name"}, {"value": "To-Core"})])
    assert records[0]["status"] == "active"
    assert result["interfaces"][0]["id"] == "i0"
    assert "To-Core" in project_graph(result)["edges"][0]["label"]


def test_wrong_type_interface_field_becomes_conflict_instead_of_breaking_inference():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    result, records = apply_overrides(snapshot, [override("override_field", {"kind": "interface", "id": "i0", "field": "kind"}, {"value": {"bad": "value"}})])
    assert records[0]["status"] == "conflict"
    assert len(result["links"]) == 1
    assert result["interfaces"][0]["kind"] == "physical"


def test_shared_member_replay_guard_detects_other_members_change_with_local_evidence():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24")
    inferred = infer(snapshot)
    link = next(l for l in inferred["links"] if l["interface_a_id"] == "i0")
    result, records = apply_overrides(snapshot, [override("suppress_link", {"link_id": link["id"]})])
    assert records[0]["status"] == "active"
    assert len(link["evidence"][0]["interface_ids"]) == 1
    snapshot["id"] = "snapshot-2"
    snapshot["interfaces"][2]["addresses"][0]["address"] = "10.0.0.4"
    result, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "conflict"
    assert next(l for l in result["links"] if l["id"] == link["id"])["review_state"] == "pending"


@pytest.mark.parametrize("operation, expected", [("confirm_link", "confirmed"), ("suppress_link", "rejected")])
def test_legacy_pair_review_migrates_only_when_complete_evidence_is_unchanged(operation, expected):
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    # Captured with the original, pre-membership-digest premise algorithm.
    legacy_hash = "premise-d5fa3e12f606398dc9ef"
    record = override(operation, link_target(snapshot), baseline={"premise": legacy_hash})
    snapshot["id"] = "snapshot-2"
    for interface in snapshot["interfaces"]:
        interface["addresses"][0]["source_refs"][0]["line"] += 100
    result, records = apply_overrides(snapshot, [record])
    assert records[0]["status"] == "active"
    assert result["links"][0]["review_state"] == expected
    assert records[0]["baseline"]["premise"] != legacy_hash
    assert records[0]["baseline"]["relation"]["evidence"][0]["membership_digest"]
    assert apply_overrides(snapshot, records) == (result, records)
    changed = topology("10.0.1.1/30", "10.0.1.2/30")
    changed["id"] = "snapshot-3"
    result, stale = apply_overrides(changed, [record])
    assert stale[0]["status"] == "conflict"
    assert stale[0]["baseline"]["premise"] == legacy_hash
    assert result["links"][0]["review_state"] == "pending"


def test_legacy_shared_review_migrates_full_group_evidence_then_guards_other_members():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24")
    legacy_hash = "premise-046d40fdba63713b3eaa"
    target = {"link_id": "member-c8e0749b8c669f772ac1"}
    record = override("suppress_link", target, baseline={"premise": legacy_hash})
    snapshot["id"] = "snapshot-2"
    result, records = apply_overrides(snapshot, [record])
    assert records[0]["status"] == "active"
    assert records[0]["baseline"]["premise"] != legacy_hash
    migrated_evidence = records[0]["baseline"]["relation"]["evidence"][0]
    assert migrated_evidence["interface_ids"] == ["i0"]
    assert next(l for l in result["links"] if l["id"] == target["link_id"])["review_state"] == "rejected"
    snapshot["interfaces"][2]["addresses"][0]["address"] = "10.0.0.4"
    snapshot["id"] = "snapshot-3"
    result, records = apply_overrides(snapshot, records)
    assert records[0]["status"] == "conflict"
    assert next(l for l in result["links"] if l["id"] == target["link_id"])["review_state"] == "pending"


def test_legacy_endpoint_rebind_preserves_manual_replacement_after_migration():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30", "10.1.0.1/30")
    record = override("rebind_endpoint", link_target(snapshot), {"endpoint": "b", "interface_id": "i2"},
                      baseline={"premise": "premise-d5fa3e12f606398dc9ef"})
    result, records = apply_overrides(snapshot, [record])
    assert records[0]["status"] == "active"
    assert next(l for l in result["links"] if l["origin"] == "inferred")["review_state"] == "rejected"
    manual = next(l for l in result["links"] if l["origin"] == "manual")
    assert {manual["interface_a_id"], manual["interface_b_id"]} == {"i0", "i2"}


@pytest.mark.parametrize("addresses", [("10.0.0.1/24", "10.0.0.2/24", "10.0.0.4/24"),
                                       ("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24", "10.0.0.4/24")])
def test_legacy_shared_review_rejects_changed_other_member_before_migration(addresses):
    snapshot = topology(*addresses)
    snapshot["id"] = "snapshot-2"
    legacy_hash = "premise-046d40fdba63713b3eaa"
    target = {"link_id": "member-c8e0749b8c669f772ac1"}
    result, records = apply_overrides(snapshot, [override("suppress_link", target, baseline={"premise": legacy_hash})])
    assert records[0]["status"] == "conflict"
    assert records[0]["baseline"]["premise"] == legacy_hash
    assert next(l for l in result["links"] if l["id"] == target["link_id"])["review_state"] == "pending"


def test_bulk_descriptive_corrections_preserve_inference_and_atomic_field_conflicts(monkeypatch):
    from backend.app import overrides as engine
    snapshot = topology(*[f"10.0.{i // 2}.{i % 2 + 1}/30" for i in range(100)])
    original = deepcopy(snapshot)
    records = [override("override_field", {"kind": "device", "id": f"d{i}", "field": "hostname"}, {"value": f"Site-{i}"}, id=f"rename-{i}") for i in range(20)]
    records.append(override("override_field", {"kind": "interface", "id": "i0", "field": "description"}, {"value": []}, id="invalid-description"))
    calls = []
    actual_infer = engine.infer
    def measured_infer(*args, **kwargs):
        calls.append(1)
        return actual_infer(*args, **kwargs)
    monkeypatch.setattr(engine, "infer", measured_infer)
    result, records = engine.apply_overrides(snapshot, records)
    assert len(calls) == 1  # Bulk labels cannot multiply full topology calculation.
    assert len(result["links"]) == 50
    assert all(result["devices"][i]["hostname"] == f"Site-{i}" for i in range(20))
    assert records[-1]["status"] == "conflict"
    assert "description" not in result["interfaces"][0]
    assert snapshot == original
