"""Manual port endpoints must use configured routing facts, not UI defaults."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.routing.model import PortSimulationRequest
from backend.app.routing.simulation import simulate_ports
from backend.tests.test_routing import lab, query
from backend.tests.test_multi_vendor_igp import mixed_lab


def port_query(snapshot, protocol="isis", source_port="GigabitEthernet0/0/1", destination_port="GigabitEthernet0/0/0", version=1):
    names = {d["hostname"]: d["id"] for d in snapshot["devices"]}
    source = next(i for i in snapshot["interfaces"] if i["device_id"] == names["IGP-S"] and i["raw_name"] == source_port)
    destination = next(i for i in snapshot["interfaces"] if i["device_id"] == names["IGP-D"] and i["raw_name"] == destination_port)
    return PortSimulationRequest(version=version, protocol=protocol,
        source_device_id=source["device_id"], source_interface_id=source["id"],
        destination_device_id=destination["device_id"], destination_interface_id=destination["id"])


@pytest.mark.parametrize("protocol", ["ospf", "isis"])
def test_transit_destination_uses_connected_last_hop_without_double_charging(protocol):
    snapshot, _ = lab()
    before = deepcopy(snapshot)
    request = port_query(snapshot, protocol)
    result, = simulate_ports(snapshot, request)["results"]
    assert result["status"] == "ok"
    assert result["total_cost"] == 30
    assert result["path_count"] == "1"
    assert result["paths"][0]["device_ids"] == ["01-S", "02-A", "06-D"]
    assert result["hops"][-1]["peer_interface_id"] == request.destination_interface_id
    # The chosen source is on the B-facing interface. It is a source address,
    # not a forced egress: the route to D's A-facing port correctly goes via A.
    assert result["hops"][0]["interface_id"] != request.source_interface_id
    assert result["source_addresses"] == ["10.80.3.1"]
    assert snapshot == before


def test_loopback_is_optional_and_retains_ecmp_when_explicitly_chosen():
    snapshot, _ = lab()
    result, = simulate_ports(snapshot, port_query(snapshot, destination_port="LoopBack0"))["results"]
    assert result["total_cost"] == 30 and result["path_count"] == "3"
    assert sorted(p["share_percent"] for p in result["paths"]) == [25, 25, 50]
    assert all(p["device_ids"][-2:] != ["06-D", "06-D"] for p in result["paths"])


def test_isis_only_configuration_never_creates_ospf():
    snapshot, _ = lab()
    facts = snapshot["routing"]
    facts["instances"] = [i for i in facts["instances"] if i["protocol"] == "isis"]
    ids = {i["id"] for i in facts["instances"]}
    facts["bindings"] = [b for b in facts["bindings"] if b["instance_id"] in ids]
    bids = {b["id"] for b in facts["bindings"]}
    facts["advertisements"] = [a for a in facts["advertisements"] if a["binding_id"] in bids]
    request = port_query(snapshot, protocol=None)
    response = simulate_ports(snapshot, request)
    assert {r["protocol"] for r in response["results"]} == {"isis"}
    with pytest.raises(ValueError, match="共同路由协议"):
        simulate_ports(snapshot, request.model_copy(update={"protocol": "ospf"}))


def test_ports_cannot_cross_device_or_routing_context_or_use_disabled_members():
    snapshot, _ = lab()
    request = port_query(snapshot)
    with pytest.raises(ValueError, match="不属于所选设备"):
        simulate_ports(snapshot, request.model_copy(update={"source_interface_id": request.destination_interface_id}))
    source = next(i for i in snapshot["interfaces"] if i["id"] == request.source_interface_id)
    for change in ({"admin_state": "disabled"}, {"aggregate_interface_id": "aggregate"}):
        modified = deepcopy(snapshot)
        next(i for i in modified["interfaces"] if i["id"] == source["id"]).update(change)
        with pytest.raises(ValueError, match="有效的三层端口"):
            simulate_ports(modified, request)
    snapshot["contexts"].append({"id": "isolated", "device_id": source["device_id"], "local_name": "isolated"})
    source["routing_context_id"] = "isolated"
    with pytest.raises(ValueError, match="共同路由协议"):
        simulate_ports(snapshot, request)


def test_missing_protocol_facts_and_unadvertised_destination_do_not_fabricate_paths():
    snapshot, _ = lab()
    request = port_query(snapshot)
    snapshot["routing"]["advertisements"] = [a for a in snapshot["routing"]["advertisements"] if a["interface_id"] != request.destination_interface_id]
    with pytest.raises(ValueError, match="目的前缀发布"):
        simulate_ports(snapshot, request)
    snapshot["routing"] = {}
    with pytest.raises(ValueError, match="重新导入"):
        simulate_ports(snapshot, request)


def test_independent_protocol_scopes_are_evaluated_separately():
    snapshot, _ = lab()
    results = simulate_ports(snapshot, port_query(snapshot, protocol=None, destination_port="LoopBack0"))["results"]
    assert {r["protocol"] for r in results} == {"ospf", "isis"}
    assert len(results) == 2 and all(r["path_count"] == "3" for r in results)


def test_secondary_destination_address_does_not_use_a_different_connected_subnet():
    snapshot, _ = lab()
    request = port_query(snapshot)
    iface = next(i for i in snapshot["interfaces"] if i["id"] == request.destination_interface_id)
    address = {**deepcopy(iface["addresses"][0]), "id": "secondary-address", "address": "10.99.8.2",
               "canonical_network": "10.99.8.0/30", "role": "secondary"}
    iface["addresses"].append(address)
    ad = next(a for a in snapshot["routing"]["advertisements"] if a["interface_id"] == iface["id"]
              and next(b for b in snapshot["routing"]["bindings"] if b["id"] == a["binding_id"])["scope"] == "2")
    snapshot["routing"]["advertisements"].append({**deepcopy(ad), "id": "secondary-advertisement",
        "address_id": address["id"], "address": address["address"], "prefix": address["canonical_network"]})
    results = {r["destination_address"]: r for r in simulate_ports(snapshot, request)["results"]}
    assert results["10.80.2.2"]["total_cost"] == 30
    assert results["10.99.8.2"]["total_cost"] == 50


def test_two_isis_levels_are_resolved_without_selectors_and_never_combined():
    snapshot, _ = lab()
    facts = snapshot["routing"]
    instance_ids = {i["id"] for i in facts["instances"] if i["protocol"] == "isis"}
    for instance in facts["instances"]:
        if instance["id"] in instance_ids:
            instance["levels"] = ["1", "2"]
    level_one = {}
    for binding in list(facts["bindings"]):
        if binding["instance_id"] in instance_ids:
            cloned = {**deepcopy(binding), "id": binding["id"] + "-l1", "scope": "1"}
            level_one[binding["id"]] = cloned["id"]
            facts["bindings"].append(cloned)
    for ad in list(facts["advertisements"]):
        if ad["binding_id"] in level_one:
            facts["advertisements"].append({**deepcopy(ad), "id": ad["id"] + "-l1", "binding_id": level_one[ad["binding_id"]]})
    results = simulate_ports(snapshot, port_query(snapshot, destination_port="LoopBack0"))["results"]
    assert {r["scope"] for r in results} == {"1", "2"}
    assert all(r["path_count"] == "3" and r["total_cost"] == 30 for r in results)


def test_mixed_vendor_interfaces_use_the_same_engine():
    snapshot = mixed_lab()
    source = next(d for d in snapshot["devices"] if d["hostname"] == "IGP-S")
    destination = next(d for d in snapshot["devices"] if d["hostname"] == "IGP-D")
    src = next(i for i in snapshot["interfaces"] if i["device_id"] == source["id"] and i["kind"] == "loopback")
    dst = next(i for i in snapshot["interfaces"] if i["device_id"] == destination["id"] and i["kind"] == "loopback")
    request = PortSimulationRequest(version=1, protocol="isis", source_device_id=source["id"],
        source_interface_id=src["id"], destination_device_id=destination["id"], destination_interface_id=dst["id"])
    result, = simulate_ports(snapshot, request)["results"]
    assert result["total_cost"] == 30 and result["path_count"] == "3"


def test_port_api_preserves_versions_and_legacy_agent_requests(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        p = client.post("/api/projects", json={"name": "Manual ports"}).json()
        assert client.post(f"/api/projects/{p['id']}/demo?kind=huawei-igp").status_code == 202
        p = client.get(f"/api/projects/{p['id']}").json()
        request = port_query(p["snapshot"], version=p["version"])
        endpoint = f"/api/projects/{p['id']}/simulate"
        response = client.post(endpoint, json=request.model_dump())
        assert response.status_code == 200
        assert response.json()["results"][0]["total_cost"] == 30
        assert client.post(endpoint, json=request.model_copy(update={"version": 1}).model_dump()).status_code == 409
        assert client.post(endpoint, json=request.model_copy(update={"source_device_id": "other-project"}).model_dump()).status_code == 422
        legacy = query(p["snapshot"], version=p["version"])
        assert client.post(endpoint, json=legacy.model_dump()).json()["path_count"] == "3"
