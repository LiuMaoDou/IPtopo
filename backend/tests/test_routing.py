"""Routing contract, independent protocol engine, and API lifecycle acceptance."""
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app.inference import infer
from backend.app.main import create_app
from backend.app.parser import parse_config
from backend.app.routing.model import RoutingFacts, SimulationRequest
from backend.app.routing.simulation import simulate

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "huawei-igp"


def lab(protocol="ospf", scope="0.0.0.0"):
    snapshot = {"id": "lab", "devices": [], "interfaces": [], "contexts": [], "routing": RoutingFacts().model_dump()}
    for path in sorted(SAMPLES.glob("*.cfg")):
        parsed = parse_config(path.read_text(encoding="utf-8"), path.name, path.name, path.stem)
        snapshot["devices"].append(parsed["device"])
        for key in ("interfaces", "contexts"):
            snapshot[key].extend(parsed[key])
        for key in ("instances", "bindings", "advertisements", "issues"):
            snapshot["routing"][key].extend(parsed["routing"][key])
        snapshot["routing"]["adapter_devices"].extend(parsed["routing"]["adapter_devices"])
    snapshot.update(infer(snapshot))
    return snapshot, query(snapshot, protocol, scope)


def query(snapshot, protocol="ospf", scope="0.0.0.0", source="IGP-S", destination="IGP-D", version=1):
    names = {d["hostname"]: d["id"] for d in snapshot["devices"]}
    source_inst = next(i for i in snapshot["routing"]["instances"] if i["protocol"] == protocol and i["device_id"] == names[source])
    instances = {i["id"] for i in snapshot["routing"]["instances"] if i["protocol"] == protocol}
    bids = {b["id"] for b in snapshot["routing"]["bindings"] if b["scope"] == scope and b["instance_id"] in instances}
    target = next(a for a in snapshot["routing"]["advertisements"] if a["device_id"] == names[destination] and a["prefix"].endswith("/32") and a["binding_id"] in bids)
    return SimulationRequest(version=version, protocol=protocol, scope=scope, source_instance_id=source_inst["id"], destination_advertisement_id=target["id"])


@pytest.mark.parametrize("protocol,scope", [("ospf", "0.0.0.0"), ("isis", "2")])
def test_ecmp_uses_local_next_hops_not_equal_end_to_end_paths(protocol, scope):
    snapshot, request = lab(protocol, scope)
    before = deepcopy(snapshot)
    result = simulate(snapshot, request)
    assert result["status"] == "ok"
    assert result["total_cost"] == 30
    assert result["path_count"] == "3"
    assert sorted(p["share_percent"] for p in result["paths"]) == [25, 25, 50]
    assert sum(h["share_percent"] for h in result["hops"] if h["source_device_id"] == result["source_device_id"]) == 100
    assert snapshot == before


def test_truncating_path_listing_does_not_change_shares_or_total_count():
    snapshot, request = lab()
    full = simulate(snapshot, request)
    request.path_limit = 1
    limited = simulate(snapshot, request)
    assert limited["path_count"] == "3" and len(limited["paths"]) == 1
    assert limited["paths_truncated"]
    assert limited["hops"] == full["hops"]


def test_reverse_direction_uses_its_own_interface_costs():
    snapshot, request = lab()
    # Change only the source-facing exits on A and B.
    ids = {i["id"] for i in snapshot["interfaces"] if i["device_id"] in ("02-A", "03-B") and i["raw_name"] == "GigabitEthernet0/0/0"}
    for b in snapshot["routing"]["bindings"]:
        if b["scope"] == request.scope and b["interface_id"] in ids:
            b["cost"] = 40
    assert simulate(snapshot, request)["total_cost"] == 30
    reverse = query(snapshot, source="IGP-D", destination="IGP-S")
    assert simulate(snapshot, reverse)["total_cost"] == 60


def test_unknown_metric_blocks_even_if_another_known_path_exists():
    snapshot, request = lab()
    b = next(b for b in snapshot["routing"]["bindings"] if b["device_id"] == "03-B" and b["scope"] == request.scope and not b["passive"])
    b["cost"] = None
    result = simulate(snapshot, request)
    assert result["status"] == "incomplete" and result["paths"] == []
    request.scenario.unknown_cost = 10
    assert simulate(snapshot, request)["status"] == "ok"


def test_interface_failure_and_metric_override_do_not_mutate_facts():
    snapshot, request = lab()
    before = deepcopy(snapshot)
    first = simulate(snapshot, request)
    via_a = next(h for h in first["hops"] if h["source_device_id"] == "01-S" and h["target_device_id"] == "02-A")
    request.scenario.disabled_interfaces = [via_a["interface_id"]]
    failed = simulate(snapshot, request)
    assert failed["path_count"] == "2"
    assert [p["share_percent"] for p in failed["paths"]] == [50, 50]
    request.scenario.disabled_interfaces = []
    request.scenario.metric_overrides = {via_a["binding_id"]: 100}
    assert simulate(snapshot, request)["path_count"] == "2"
    assert snapshot == before


def test_ecmp_limit_reports_ambiguous_hardware_subset():
    snapshot, request = lab()
    next(i for i in snapshot["routing"]["instances"] if i["id"] == request.source_instance_id)["max_paths"] = 1
    result = simulate(snapshot, request)
    assert result["forwarding_status"] == "limit_ambiguous"
    assert result["path_count"] == "3"
    assert any(d["code"] == "ecmp_limit" for d in result["diagnostics"])


def test_engine_does_not_depend_on_vendor_metadata_or_local_process_names():
    snapshot, request = lab()
    expected = simulate(snapshot, request)
    for device in snapshot["devices"]:
        device["vendor"] = "Independent adapter"
        device["os"] = "Independent OS"
    for instance in snapshot["routing"]["instances"]:
        instance["local_name"] = "arbitrary-local-name"
    assert simulate(snapshot, request) == expected


def test_parallel_l3_links_are_separate_next_hops():
    snapshot, request = lab()
    original = next(l for l in snapshot["links"] if {l["device_a_id"], l["device_b_id"]} == {"01-S", "02-A"})
    duplicate = deepcopy(original)
    duplicate["id"] = "parallel-link"
    for side in ("a", "b"):
        iid = original[f"interface_{side}_id"]
        iface = deepcopy(next(i for i in snapshot["interfaces"] if i["id"] == iid))
        iface["id"] += "parallel"
        iface["raw_name"] = "GigabitEthernet0/0/99"
        snapshot["interfaces"].append(iface)
        binding = deepcopy(next(b for b in snapshot["routing"]["bindings"] if b["interface_id"] == iid and b["scope"] == request.scope))
        binding["id"] += "parallel"
        binding["interface_id"] = iface["id"]
        snapshot["routing"]["bindings"].append(binding)
        duplicate[f"interface_{side}_id"] = iface["id"]
    snapshot["links"].append(duplicate)
    result = simulate(snapshot, request)
    assert result["path_count"] == "4"
    assert len([h for h in result["hops"] if h["source_device_id"] == "01-S"]) == 3
    assert sorted(round(p["share_percent"], 2) for p in result["paths"]) == [16.67, 16.67, 33.33, 33.33]


def test_disabled_destination_and_same_device():
    snapshot, request = lab()
    target = next(a for a in snapshot["routing"]["advertisements"] if a["id"] == request.destination_advertisement_id)
    request.scenario.disabled_interfaces = [target["interface_id"]]
    assert simulate(snapshot, request)["status"] == "unreachable"
    local = query(snapshot, destination="IGP-S")
    result = simulate(snapshot, local)
    assert result["total_cost"] == 0 and result["path_count"] == "1" and result["hops"] == []


def test_huawei_network_wildcard_passive_and_isis_per_level_metrics():
    config = """sysname R1
#
ospf 9 router-id 1.1.1.1
 silent-interface all
 undo silent-interface GigabitEthernet 0/0/0
 area 1
  network 10.0.0.0 0.0.0.255
#
isis 7
 network-entity 49.0001.0000.0000.0001.00
 cost-style wide
 circuit-cost 20 level-1
#
interface GigabitEthernet0/0/0
 ip address 10.0.0.1 255.255.255.252
 ospf cost 5
 isis enable 7
 isis cost 30 level-2
#
interface LoopBack0
 ip address 10.0.0.99 255.255.255.255
 isis enable 7
#
"""
    parsed = parse_config(config, "r.cfg", "source", "device")
    facts = parsed["routing"]
    assert not facts["issues"]
    ospf = [b for b in facts["bindings"] if b["scope"] == "0.0.0.1"]
    assert len(ospf) == 2 and sum(b["passive"] for b in ospf) == 1
    assert {b["cost"] for b in facts["bindings"] if b["scope"] == "1"} == {20}
    assert {b["cost"] for b in facts["bindings"] if b["scope"] == "2"} == {0, 30}
    assert all(b["source_refs"] for b in facts["bindings"])


def test_global_isis_system_id_command_is_not_a_process_declaration():
    config = (SAMPLES / "01-S.cfg").read_text(encoding="utf-8") + "isis system-id auto-recover disable\n#\n"
    parsed = parse_config(config, "r.cfg", "source", "device")
    assert len(parsed["routing"]["instances"]) == 2
    assert not parsed["routing"]["issues"]


def test_unsupported_authentication_is_diagnosed_without_exposing_secret():
    config = (SAMPLES / "01-S.cfg").read_text(encoding="utf-8").replace(" area 0.0.0.0", " area 0.0.0.0\n  authentication-mode md5 1 cipher PRIVATE-SECRET", 1)
    parsed = parse_config(config, "r.cfg", "source", "device")
    assert parsed["routing"]["issues"][0]["code"] == "unmodeled_authentication"
    assert parsed["routing"]["issues"][0]["blocking"] is False
    assert "PRIVATE-SECRET" not in str(parsed["routing"])


def test_shared_broadcast_segment_charges_one_sender_metric_and_p2p_is_ambiguous():
    snapshot = {"id": "lan", "devices": [], "interfaces": [], "contexts": [], "routing": RoutingFacts().model_dump()}
    for index, name in enumerate(("S", "A", "D"), 1):
        config = f"""sysname IGP-{name}
#
ospf {index} router-id 10.255.0.{index}
#
interface LoopBack0
 ip address 10.255.0.{index} 255.255.255.255
 ospf enable {index} area 0
#
interface GigabitEthernet0/0/0
 ip address 10.0.0.{index} 255.255.255.0
 ospf enable {index} area 0
 ospf cost 10
#
"""
        parsed = parse_config(config, name, name, name)
        snapshot["devices"].append(parsed["device"])
        for key in ("interfaces", "contexts"):
            snapshot[key].extend(parsed[key])
        for key in ("instances", "bindings", "advertisements", "issues", "adapter_devices"):
            snapshot["routing"][key].extend(parsed["routing"][key])
    snapshot.update(infer(snapshot))
    result = simulate(snapshot, query(snapshot))
    assert result["total_cost"] == 10 and result["path_count"] == "1"
    assert len(result["hops"][0]["link_ids"]) == 2
    for b in snapshot["routing"]["bindings"]:
        if not b["passive"]:
            b["network_type"] = "p2p"
    assert simulate(snapshot, query(snapshot))["status"] == "incomplete"


def test_unparsed_peer_and_duplicate_router_id_are_not_silently_ignored():
    snapshot, request = lab()
    snapshot["routing"]["adapter_devices"].remove("02-A")
    assert simulate(snapshot, request)["status"] == "incomplete"
    snapshot, request = lab()
    ospf = [i for i in snapshot["routing"]["instances"] if i["protocol"] == "ospf"]
    ospf[1]["router_id"] = ospf[0]["router_id"]
    assert simulate(snapshot, request)["status"] == "incomplete"


def test_protocol_blocker_does_not_block_other_protocol():
    snapshot, request = lab("isis", "2")
    inst = next(i for i in snapshot["routing"]["instances"] if i["device_id"] == "01-S" and i["protocol"] == "ospf")
    snapshot["routing"]["issues"].append({"code": "unsupported", "message": "Test unsupported OSPF policy", "device_id": "01-S", "instance_id": inst["id"], "protocol": "ospf"})
    assert simulate(snapshot, request)["status"] == "ok"
    assert simulate(snapshot, query(snapshot))["status"] == "incomplete"


def test_invalid_model_references_and_scenario_values_are_rejected():
    snapshot, request = lab()
    request.scenario.metric_overrides = {"unknown_binding": 10}
    with pytest.raises(ValueError, match="开销覆盖"):
        simulate(snapshot, request)
    snapshot["routing"]["bindings"][0]["instance_id"] = "missing_instance"
    with pytest.raises(ValueError, match="实例引用"):
        RoutingFacts.model_validate(snapshot["routing"])


def test_isis_l1_requires_common_area_but_l2_does_not():
    snapshot, request = lab("isis", "2")
    for index, instance in enumerate(snapshot["routing"]["instances"]):
        if instance["protocol"] == "isis":
            instance["areas"] = [f"49{index:04}"]
    assert simulate(snapshot, request)["status"] == "ok"
    for binding in snapshot["routing"]["bindings"]:
        if binding["scope"] == "2":
            binding["scope"] = "1"
    request.scope = "1"
    assert simulate(snapshot, request)["status"] == "unreachable"


def test_snapshot_import_merge_replace_restore_and_simulation_version(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        p = client.post("/api/projects", json={"name": "Routing lab"}).json()
        assert client.post(f"/api/projects/{p['id']}/demo?kind=huawei-igp").status_code == 202
        p = client.get(f"/api/projects/{p['id']}").json()
        req = query(p["snapshot"], version=p["version"])
        endpoint = f"/api/projects/{p['id']}/simulate"
        response = client.post(endpoint, json=req.model_dump())
        assert response.status_code == 200 and response.json()["path_count"] == "3"
        stale = req.model_copy(update={"version": p["version"] - 1})
        assert client.post(endpoint, json=stale.model_dump()).status_code == 409
        source = SAMPLES / "01-S.cfg"
        imported = client.post(f"/api/projects/{p['id']}/imports", data={"expected_version": p["version"]}, files={"files": (source.name, source.read_bytes())})
        assert imported.status_code == 202
        merged = client.get(f"/api/projects/{p['id']}").json()
        assert len(merged["snapshot"]["routing"]["instances"]) == 12
        assert client.post(endpoint, json=query(merged["snapshot"], version=merged["version"]).model_dump()).json()["path_count"] == "3"
        bundle = client.get(f"/api/projects/{p['id']}/exports/json").content
        restored = client.post("/api/projects/restore", files={"file": ("lab.json", bundle)})
        assert restored.status_code == 201
        restored_p = restored.json()
        assert len(restored_p["snapshot"]["routing"]["instances"]) == 12
        assert client.post(f"/api/projects/{restored_p['id']}/simulate", json=query(restored_p["snapshot"], version=restored_p["version"]).model_dump()).json()["path_count"] == "3"
        replaced = client.post(f"/api/projects/{p['id']}/imports", data={"expected_version": merged["version"], "strategy": "replace"}, files={"files": (source.name, source.read_bytes())})
        assert replaced.status_code == 202
        final = client.get(f"/api/projects/{p['id']}").json()
        assert len(final["snapshot"]["routing"]["instances"]) == 2
