from copy import deepcopy
import ipaddress
import time

import pytest

from backend.app.inference import infer, project_graph


def topology(*addresses):
    """One interface per device; each argument may be a dual-stack address list."""
    snapshot = {"id": "snapshot-1", "devices": [], "interfaces": [], "contexts": [], "diagnostics": [], "parser_versions": {"fixture": "1"}}
    for index, configured in enumerate(addresses):
        device, interface, context = f"d{index}", f"i{index}", f"c{index}"
        snapshot["devices"].append({"id": device, "hostname": f"router-{index}", "parse_status": "success"})
        snapshot["contexts"].append({"id": context, "device_id": device, "kind": "default", "local_name": "default"})
        interface_record = {"id": interface, "device_id": device, "routing_context_id": context, "raw_name": "Gi0/0", "kind": "physical", "admin_state": "enabled", "effective_state": "active", "oper_state": "unknown", "addresses": []}
        for number, address in enumerate([configured] if isinstance(configured, str) else configured):
            ipi = ipaddress.ip_interface(address)
            interface_record["addresses"].append({"id": f"a{index}-{number}", "interface_id": interface, "family": ipi.version, "address": str(ipi.ip), "prefix_length": ipi.network.prefixlen, "canonical_network": str(ipi.network), "role": "primary", "acquisition_method": "static", "effective_state": "active", "source_refs": [{"source_file_id": f"f{index}", "line": 10 + number, "text": f"ip address {address}", "parser": "fixture"}]})
        snapshot["interfaces"].append(interface_record)
    return snapshot


def codes(inferred):
    return {d["code"] for d in inferred["diagnostics"]}


@pytest.mark.parametrize("left,right,family", [("10.0.0.0/31", "10.0.0.1/31", 4), ("2001:db8::/127", "2001:db8::1/127", 6)])
def test_at_01_02_point_to_point(left, right, family):
    result = infer(topology(left, right))
    assert len(result["links"]) == 1
    assert result["links"][0]["families"] == [family]
    assert result["links"][0]["review_state"] == "pending"
    assert len(result["links"][0]["evidence"][0]["source_refs"]) == 2
    assert "text" not in result["links"][0]["evidence"][0]["source_refs"][0]
    assert "default_domain_assumption" in codes(result)


def test_at_03_dual_stack_exact_endpoint_merge_and_evidence():
    snapshot = topology(["10.0.0.0/31", "2001:db8::/127"], ["10.0.0.1/31", "2001:db8::1/127"])
    result = infer(snapshot)
    assert len(result["links"]) == 1
    assert result["links"][0]["families"] == [4, 6]
    assert len(result["links"][0]["evidence"]) == 2
    assert len(result["segments"]) == 2
    graph = project_graph({**snapshot, **result}, "segment")
    assert len(graph["nodes"]) == 4
    assert len(graph["edges"]) == 4


def test_at_04_shared_prefix_is_never_clique():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24")
    result = infer(snapshot)
    assert len(result["segments"]) == 1
    assert len(result["links"]) == 3
    assert all(link["relation_type"] == "segment_member" for link in result["links"])
    for mode in ("device", "segment"):
        graph = project_graph({**snapshot, **result}, mode)
        assert len(graph["nodes"]) == 4
        assert len(graph["edges"]) == 3
        assert {e["target"] for e in graph["edges"]} == {result["segments"][0]["id"]}


def test_at_05_same_named_vrfs_isolated_then_explicit_customer_domains():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30", "10.0.0.1/30", "10.0.0.2/30")
    for context in snapshot["contexts"]:
        context.update(kind="vrf", local_name="customer", rd="1:1", rt=["1:1"])
    assert infer(snapshot)["links"] == []
    settings = {"mappings": {"c0": "customer-a", "c1": "customer-a", "c2": "customer-b", "c3": "customer-b"}}
    result = infer(snapshot, settings)
    assert len(result["links"]) == 2
    assert "duplicate_ip" not in codes(result)


def test_at_06_pe_vrf_ce_default_mapped_explicitly():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    snapshot["contexts"][0].update(kind="vrf", local_name="customer")
    assert not infer(snapshot)["links"]
    assert len(infer(snapshot, {"interface_mappings": {"i0": "ce", "i1": "ce"}})["links"]) == 1


def test_at_07_parallel_interfaces_remain_distinct():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30", "10.0.1.1/30", "10.0.1.2/30")
    snapshot["interfaces"][2]["device_id"] = "d0"
    snapshot["interfaces"][3]["device_id"] = "d1"
    snapshot["devices"] = snapshot["devices"][:2]
    result = infer(snapshot)
    assert len(result["links"]) == 2
    assert len({link["id"] for link in result["links"]}) == 2
    graph = project_graph({**snapshot, **result})
    assert len(graph["edges"]) == 2
    assert len({(e["source"], e["target"]) for e in graph["edges"]}) == 1


def test_at_08_link_local_and_host_prefixes_excluded():
    result = infer(topology(["fe80::1/64", "10.0.0.1/32"], ["fe80::2/64", "10.0.0.1/32"]))
    assert result["links"] == []
    assert {u["reason"] for u in result["unmatched"]} == {"link_local", "host_prefix"}


def test_at_09_vip_not_extra_endpoint_or_false_duplicate():
    snapshot = topology(["10.0.0.1/24", "10.0.0.254/24"], ["10.0.0.2/24", "10.0.0.254/24"])
    for interface in snapshot["interfaces"]:
        interface["addresses"][1]["role"] = "virtual"
    result = infer(snapshot)
    assert len(result["links"]) == 1
    assert "duplicate_ip" not in codes(result)
    assert len(result["links"][0]["evidence"][0]["addresses"]) == 2


def test_at_10_overlapping_masks_are_diagnostic_only():
    result = infer(topology("10.0.0.1/24", "10.0.0.2/25"))
    assert not result["links"]
    assert len(result["segments"]) == 2
    assert "prefix_overlap" in codes(result)


def test_at_11_aggregate_members_are_not_address_endpoints():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    snapshot["interfaces"][0].update(kind="aggregate", raw_name="Port-channel1", aggregate_members=["i2", "i3"])
    snapshot["interfaces"].extend([{"id": f"i{i}", "device_id": "d0", "raw_name": f"Gi{i}", "kind": "physical", "addresses": []} for i in (2, 3)])
    result = infer(snapshot)
    assert len(result["links"]) == 1
    assert result["links"][0]["interface_a_id"] == "i0"


def test_at_13_shutdown_and_inactive_preserved_but_excluded():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24")
    snapshot["interfaces"][1]["admin_state"] = "disabled"
    snapshot["interfaces"][2]["addresses"][0]["effective_state"] = "inactive"
    before = deepcopy(snapshot)
    result = infer(snapshot)
    assert result["links"] == []
    assert snapshot == before
    assert all(i["oper_state"] == "unknown" for i in snapshot["interfaces"])
    assert [u["reason"] for u in result["unmatched"]].count("inactive") == 2


def test_duplicate_ip_never_creates_false_link_even_with_different_masks():
    for suffixes in ((24, 24), (24, 25)):
        result = infer(topology(f"10.0.0.1/{suffixes[0]}", f"10.0.0.1/{suffixes[1]}"))
        assert not result["links"]
        assert "duplicate_ip" in codes(result)


def test_same_interface_multiple_addresses_is_single_endpoint_and_same_device_no_loop():
    snapshot = topology(["10.0.0.1/24", "10.0.0.2/24"], "10.0.0.3/24")
    assert len(infer(snapshot)["links"]) == 1
    snapshot["interfaces"][1]["device_id"] = "d0"
    result = infer(snapshot)
    assert not result["links"]
    assert "same_device_prefix" in codes(result)


def test_different_ipv4_ipv6_peers_do_not_merge():
    result = infer(topology(["10.0.0.1/30", "2001:db8::/127"], "10.0.0.2/30", "2001:db8::1/127"))
    assert len(result["links"]) == 2
    assert "dual_stack_peer_mismatch" in codes(result)
    assert all(len(link["families"]) == 1 for link in result["links"])


def test_stable_ids_under_reordering_and_reimport():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    original = infer(snapshot)
    snapshot["id"] = "snapshot-2"
    snapshot["interfaces"].reverse()
    snapshot["devices"].reverse()
    updated = infer(snapshot)
    assert [s["id"] for s in original["segments"]] == [s["id"] for s in updated["segments"]]
    assert [l["id"] for l in original["links"]] == [l["id"] for l in updated["links"]]


def test_loopback_anycast_unresolved_and_invalid_addresses_have_no_links():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24", "10.0.0.4/24")
    snapshot["interfaces"][0]["kind"] = "loopback"
    snapshot["interfaces"][1]["addresses"][0]["role"] = "anycast"
    snapshot["interfaces"][2]["addresses"][0].update(address=None, acquisition_method="dhcp")
    snapshot["interfaces"][3]["addresses"][0]["address"] = "999.0.0.1"
    result = infer(snapshot)
    assert not result["links"]
    assert {u["reason"] for u in result["unmatched"]} == {"loopback", "anycast", "unresolved_address", "invalid_address"}


def test_partial_parse_warning_propagates_to_link_and_graph_strips_source_text():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    snapshot["devices"][0].update(parse_status="partial", source_refs=[{"text": "sensitive config", "line": 1}])
    result = infer(snapshot)
    assert "partial_parse_evidence" in {d["code"] for d in result["links"][0]["diagnostics"]}
    graph = project_graph({**snapshot, **result})
    assert "text" not in graph["nodes"][0]["source_refs"][0]
    result["links"][0]["review_state"] = "rejected"
    for mode in ("device", "segment"):
        assert not project_graph({**snapshot, **result}, mode)["edges"]


def test_segment_split_prevents_reused_prefix_clique():
    snapshot = topology("10.0.0.1/24", "10.0.0.2/24", "10.0.0.3/24", "10.0.0.4/24")
    settings = {"segment_splits": {"default|4|10.0.0.0/24": {"i0": "site-a", "i1": "site-a", "i2": "site-b", "i3": "site-b"}}}
    result = infer(snapshot, settings)
    assert len(result["segments"]) == 2
    assert len(result["links"]) == 2
    assert all(l["relation_type"] == "ip_inferred" for l in result["links"])


def test_2000_interfaces_no_quadratic_pair_comparison():
    addresses = []
    for i in range(1000):
        network = ipaddress.ip_network((int(ipaddress.IPv4Address("10.0.0.0")) + i * 4, 30))
        addresses.extend([f"{network.network_address + 1}/30", f"{network.network_address + 2}/30"])
    snapshot = topology(*addresses)
    started = time.monotonic()
    result = infer(snapshot)
    elapsed = time.monotonic() - started
    assert len(result["links"]) == 1000
    assert elapsed < 10  # A guard against accidental enumeration, not a capacity claim.


def test_dual_stack_segment_projection_has_only_its_own_family_evidence():
    snapshot = topology(["10.0.0.0/31", "2001:db8::/127"], ["10.0.0.1/31", "2001:db8::1/127"])
    result = infer(snapshot)
    graph = project_graph({**snapshot, **result}, "segment")
    assert len(graph["edges"]) == 4
    for edge in graph["edges"]:
        assert len(edge["families"]) == len(edge["prefixes"]) == len(edge["evidence"]) == 1
        assert edge["families"] == [edge["evidence"][0]["family"]]
        assert edge["link_id"] == result["links"][0]["id"]


def test_missing_routing_context_does_not_join_default_or_other_device():
    snapshot = topology("10.0.0.1/30", "10.0.0.2/30")
    snapshot["contexts"] = []
    for interface in snapshot["interfaces"]:
        interface["routing_context_id"] = "missing-vrf"
    result = infer(snapshot)
    assert not result["links"]
    assert "missing_routing_context" in codes(result)


def test_thousand_member_segment_evidence_and_payload_scale_linearly():
    import json
    sizes = []
    for count in (100, 1000):
        snapshot = topology(*[f"10.80.{i // 254}.{i % 254 + 1}/16" for i in range(count)])
        result = infer(snapshot)
        assert len(result["segments"]) == 1
        assert len(result["links"]) == count
        assert len(result["segments"][0]["evidence"][0]["interface_ids"]) == count
        for link in result["links"]:
            evidence = link["evidence"][0]
            assert evidence["interface_ids"] == [link["interface_a_id"]]
            assert len(evidence["addresses"]) == len(evidence["source_refs"]) == 1
            assert evidence["membership_digest"] == result["segments"][0]["evidence"][0]["membership_digest"]
        sizes.append(len(json.dumps({"snapshot": result, "topology": project_graph({**snapshot, **result})})))
    assert sizes[1] < sizes[0] * 12
    assert sizes[1] < 4_000_000


@pytest.mark.parametrize("membership", ["parent_list", "member_reference", "member_unit"])
def test_aggregate_member_addresses_are_preserved_but_not_inferred(membership):
    snapshot = topology("192.0.2.1/30", "192.0.2.2/30", "192.0.2.1/30")
    snapshot["interfaces"][0].update(kind="aggregate", raw_name="ae0")
    member = snapshot["interfaces"][2]
    member["device_id"] = "d0"
    if membership == "parent_list":
        snapshot["interfaces"][0]["aggregate_members"] = [member["id"]]
    elif membership == "member_reference":
        member["aggregate_interface_id"] = "i0"
    else:
        member["parent_interface_id"] = "physical-member"
        snapshot["interfaces"].append({"id": "physical-member", "device_id": "d0", "aggregate_interface_id": "i0", "addresses": []})
    before = deepcopy(snapshot)
    result = infer(snapshot)
    assert len(result["links"]) == 1
    assert "duplicate_ip" not in codes(result)
    assert any(u["interface_id"] == member["id"] and u["reason"] == "aggregate_member" for u in result["unmatched"])
    assert snapshot == before
