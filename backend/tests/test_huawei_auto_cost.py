"""Automatic costs follow confirmed formulas and retain bandwidth assumptions."""
from copy import deepcopy
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.overrides import apply_overrides
from backend.app.parser import parse_config
from backend.app.routing.adapters.auto_cost import automatic_cost
from backend.app.routing.refresh import apply_bandwidth_assumptions, refresh_huawei_routing
from backend.app.routing.model import PortSimulationRequest
from backend.app.routing.simulation import simulate_ports


def configuration(extra="", cost="", mode=""):
    return f'''sysname Auto
#
isis 1
 is-level level-2
 cost-style wide
 network-entity 49.0001.0000.0000.0001.00
 bandwidth-reference 100000
 auto-cost enable{mode}
{extra}#
interface LoopBack0
 ip address 10.255.0.1 255.255.255.255
 isis enable 1
#
interface Eth-Trunk0
 isis enable 1
{cost}#
interface Eth-Trunk0.1
 ip address 10.0.0.1 255.255.255.252
 isis enable 1
#
interface 10GE1/0/1
 eth-trunk 0
#
interface 10GE1/0/2
 eth-trunk 0
#
return
'''


def parsed(text=None):
    return parse_config(text or configuration(), "auto.cfg", "auto-source", "auto-device")


def binding_for(result, interface):
    iid = next(i["id"] for i in result["interfaces"] if i["raw_name"] == interface)
    return next(b for b in result["routing"]["bindings"] if b["interface_id"] == iid)


@pytest.mark.parametrize("mode,expected", [("", 50), (" compatible", 5)])
def test_lag_sum_subinterface_inheritance_and_loopback_exception(mode, expected):
    result = parsed(configuration(mode=mode))
    assert not any(i["code"] == "invalid_cost" for i in result["routing"]["issues"])
    for name in ("Eth-Trunk0", "Eth-Trunk0.1"):
        binding = binding_for(result, name)
        assert binding["cost"] == expected and binding["bandwidth_mbps"] == 20000
        assert binding["cost_origin"] == "bandwidth_derived"
        assert any("推演" in note for note in binding["cost_assumptions"])
    assert binding_for(result, "LoopBack0")["cost"] == 0


def test_explicit_interface_and_global_costs_take_precedence():
    result = parsed(configuration(extra=" circuit-cost 7\n", cost=" isis cost 13\n"))
    assert binding_for(result, "Eth-Trunk0")["cost"] == 13
    assert binding_for(result, "Eth-Trunk0.1")["cost"] == 7
    assert binding_for(result, "Eth-Trunk0")["cost_origin"] == "explicit"


@pytest.mark.parametrize("style,expected", [("wide-compatible",50),("narrow",10),("narrow-compatible",10),("compatible",10)])
def test_compatible_cost_styles_use_the_confirmed_formula_or_table(style, expected):
    result = parsed(configuration().replace('cost-style wide', f'cost-style {style}'))
    assert binding_for(result,'Eth-Trunk0.1')['cost'] == expected


@pytest.mark.parametrize("bandwidth,expected", [(1,60),(10,60),(11,50),(100,50),(101,40),(155,40),
    (156,30),(622,30),(623,20),(2500,20),(2501,10)])
def test_narrow_cost_table_boundaries(bandwidth, expected):
    assert automatic_cost(Fraction(bandwidth), None, "narrow") == expected


def test_missing_members_and_ambiguous_speed_stay_unknown_until_confirmed():
    text = configuration().replace("10GE1/0/1", "50|100GE1/0/1")
    result = parsed(text)
    assert binding_for(result, "Eth-Trunk0.1")["cost"] is None
    iface = next(i for i in result["interfaces"] if i["raw_name"] == "50|100GE1/0/1")
    snapshot = {"id":"snapshot", "devices":[result["device"]],
        **{key: result[key] for key in ("interfaces","contexts","routing")}}
    overrides = [{"id":"confirmed", "operation":"override_field", "status":"active",
        "target":{"kind":"interface", "id":iface["id"], "field":"assumed_bandwidth_mbps"},
        "value":{"value":50000}, "reason":"User confirmed 50 Gbit/s; offline assumption"}]
    output, evaluated = apply_overrides(snapshot, overrides)
    assert evaluated[0]["status"] == "active"
    output = apply_bandwidth_assumptions(output)
    b = binding_for(output, "Eth-Trunk0.1")
    assert b["bandwidth_mbps"] == 60000 and b["cost"] == 16
    assert any("人工确认" in a for a in b["cost_assumptions"])
    assert binding_for(snapshot, "Eth-Trunk0.1")["cost"] is None


@pytest.mark.parametrize("value", [True, 0, -1, "50000", float('inf')])
def test_bandwidth_assumption_validates_type_and_range(value):
    result = parsed()
    iface = result["interfaces"][0]
    snapshot = {"devices":[result["device"]], **{key:result[key] for key in ("interfaces","contexts","routing")}}
    _, records = apply_overrides(snapshot, [{"id":"bad", "status":"active", "operation":"override_field",
        "target":{"kind":"interface","id":iface["id"],"field":"assumed_bandwidth_mbps"},"value":{"value":value}}])
    assert records[0]["status"] == "conflict"


def test_cli_cipher_quote_is_not_a_broken_configuration_but_real_open_quote_is_reported():
    result = parsed(configuration().replace("return", 'local-user qa password irreversible-cipher ABC"XYZ\nreturn'))
    assert not any(d["code"] == "unterminated_quote" for d in result["diagnostics"])
    result = parsed(configuration().replace("return", 'description "unfinished\nreturn'))
    assert any(d["code"] == "unterminated_quote" for d in result["diagnostics"])


def test_old_routing_facts_refresh_from_original_text_without_mutating_snapshot():
    text = configuration()
    result = parsed(text)
    snapshot = {"id":"old", "devices":[result["device"]],
        **{key:result[key] for key in ("interfaces","contexts","routing")},
        "source_files":[{"id":"auto-source","filename":"auto.cfg","diagnostics":[]}]}
    snapshot["routing"]["adapter_versions"]["huawei_vrp_igp"] = "1.0.0"
    for binding in snapshot["routing"]["bindings"]:
        if binding["network_type"] != "loopback":
            binding.update(cost=10, cost_origin="profile_default", automatic_cost=None)
    before = deepcopy(snapshot)
    upgraded = refresh_huawei_routing(snapshot, lambda sid: text)
    assert binding_for(upgraded, "Eth-Trunk0.1")["cost"] == 50
    assert upgraded["id"] == snapshot["id"] and upgraded["interfaces"][0]["id"] == snapshot["interfaces"][0]["id"]
    assert snapshot == before
    assert refresh_huawei_routing(snapshot, lambda sid: None) is snapshot


def test_assumptions_and_automatic_metadata_survive_sourceless_backup_and_restore(tmp_path):
    text = configuration().replace("10GE1/0/1", "50|100GE1/0/1")
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        p = client.post('/api/projects',json={"name":"Bandwidth lab"}).json()
        response = client.post(f"/api/projects/{p['id']}/imports", data={"expected_version":p['version']}, files={"files":('auto.cfg',text)})
        assert response.status_code == 202
        p = client.get(f"/api/projects/{p['id']}").json()
        iface = next(i for i in p['snapshot']['interfaces'] if i['raw_name']=='50|100GE1/0/1')
        p = client.post(f"/api/projects/{p['id']}/overrides",json={"version":p['version'],"operation":"override_field",
            "target":{"kind":"interface","id":iface['id'],"field":"assumed_bandwidth_mbps"},"value":{"value":50000},"reason":"Confirmed simulation assumption"}).json()
        assert binding_for(p['snapshot'],'Eth-Trunk0.1')['cost'] == 16
        bundle = client.get(f"/api/projects/{p['id']}/exports/json").content
        restored = client.post('/api/projects/restore',files={"file":('lab.json',bundle)})
        assert restored.status_code == 201
        assert binding_for(restored.json()['snapshot'],'Eth-Trunk0.1')['cost'] == 16
        assert not __import__('json').loads(bundle)['sources']
        p = client.post(f"/api/projects/{p['id']}/overrides",json={"version":p['version'],"operation":"override_field",
            "target":{"kind":"interface","id":iface['id'],"field":"assumed_bandwidth_mbps"},"value":{"value":None},"reason":"Restore automatic"}).json()
        assert binding_for(p['snapshot'],'Eth-Trunk0.1')['cost'] is None
        clear = p['overrides'][-1]
        p = client.patch(f"/api/projects/{p['id']}/overrides/{clear['id']}",json={"version":p['version'],"status":"undone"}).json()
        assert binding_for(p['snapshot'],'Eth-Trunk0.1')['cost'] == 16
