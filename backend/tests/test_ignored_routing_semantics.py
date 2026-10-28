"""Basic IPv4 SPF can ignore extensions without inventing missing base facts."""
from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.inference import infer
from backend.app.parser import parse_config
from backend.app.routing.model import RoutingFacts
from backend.app.routing.simulation import simulate
from backend.tests.test_routing import lab, query

SAMPLES = Path(__file__).resolve().parents[2]/"samples"/"multi-vendor-igp"


def snapshot_with(file, transform):
    result={"id":"semantics","devices":[],"interfaces":[],"contexts":[],"routing":RoutingFacts().model_dump()}
    for path in sorted(SAMPLES.glob("*.cfg")):
        text=path.read_text(encoding="utf-8")
        if path.name==file:text=transform(text)
        parsed=parse_config(text,path.name,path.name,path.stem)
        result["devices"].append(parsed["device"])
        for key in ("interfaces","contexts"):result[key].extend(parsed[key])
        for key in ("instances","bindings","advertisements","issues","adapter_devices"):
            result["routing"][key].extend(parsed["routing"][key])
    result.update(infer(result))
    return result


EXTENSIONS = [
    ("06-D-huawei.cfg",lambda s:s.replace("ospf 6 router-id 10.255.0.6\n","ospf 6 router-id 10.255.0.6\n import-route static\n")),
    ("01-S-cisco-ios.cfg",lambda s:s.replace("router ospf 1\n","router ospf 1\n redistribute connected subnets\n")),
    ("04-C-cisco-xr.cfg",lambda s:s.replace("router ospf 1\n","router ospf 1\n prefix-suppression\n")),
    ("02-A-juniper-set.cfg",lambda s:s+"\nset protocols ospf export EXTRA-POLICY\nset protocols ospf area 0 stub\n"),
    ("03-B-nokia-classic.cfg",lambda s:s.replace("  ospf 1\n","  ospf 1\n   traffic-engineering\n")),
    ("05-E-nokia-md.cfg",lambda s:s.replace("  ospf 1 {\n","  ospf 1 {\n   traffic-engineering true\n")),
]


@pytest.mark.parametrize("file,transform",EXTENSIONS,ids=[x[0] for x in EXTENSIONS])
def test_native_vendor_extensions_keep_basic_spf_and_source_diagnostics(file,transform):
    snapshot=snapshot_with(file,transform);before=deepcopy(snapshot)
    result=simulate(snapshot,query(snapshot))
    assert result["status"]=="ok" and result["total_cost"]==30 and result["path_count"]=="3"
    warnings=[d for d in result["diagnostics"] if d.get("ignored")]
    assert warnings and all(d["blocking"] is False and d["severity"]=="warning" for d in warnings)
    assert all(d["source_refs"] and "按基础 IPv4 图计算" in d["message"] for d in warnings)
    assert any("已忽略未建模配置语义" in a for a in result["assumptions"])
    assert snapshot==before


@pytest.mark.parametrize("code",[
    "unsupported_protocol_command","unsupported_interface_command",
    "unsupported_protocol_statement","unsupported_area_statement",
    "unknown_protocol_statement","unknown_interface_command","unknown_area_statement",
])
def test_existing_snapshot_generic_blockers_are_ignored_without_reimport(code):
    snapshot,request=lab()
    inst=next(i for i in snapshot["routing"]["instances"] if i["id"]==request.source_instance_id)
    reference={"source_file_id":"legacy-source","line":17}
    snapshot["routing"]["issues"].append({"code":code,"message":"Legacy extension",
        "device_id":inst["device_id"],"instance_id":inst["id"],"protocol":"ospf","blocking":True,"source_refs":[reference]})
    before=deepcopy(snapshot)
    result=simulate(snapshot,request)
    assert result["status"]=="ok" and result["total_cost"]==30
    warning=next(d for d in result["diagnostics"] if d["code"]==code)
    assert warning["ignored"] is True and warning["blocking"] is False
    assert warning["source_refs"]==[reference] and "Legacy extension" in warning["message"]
    assert snapshot==before


@pytest.mark.parametrize("code",[
    "missing_net","missing_instance","invalid_cost","unsupported_network_type",
    "incomplete_configuration","incomplete_routing_configuration",
    "unresolved_routing_inheritance","authentication_mismatch",
])
def test_real_incomplete_facts_and_known_mismatch_are_not_downgraded(code):
    snapshot,request=lab()
    inst=next(i for i in snapshot["routing"]["instances"] if i["id"]==request.source_instance_id)
    snapshot["routing"]["issues"].append({"code":code,"message":"Missing or invalid base fact",
        "device_id":inst["device_id"],"instance_id":inst["id"],"protocol":"ospf"})
    result=simulate(snapshot,request)
    assert result["status"]=="incomplete" and not result["paths"]
    assert not next(d for d in result["diagnostics"] if d["code"]==code).get("ignored")


def test_unmodeled_authentication_is_an_explicit_assumption_without_secret():
    snapshot=snapshot_with("06-D-huawei.cfg",lambda s:s.replace(" area 0.0.0.0\n"," area 0.0.0.0\n  authentication-mode md5 1 cipher PRIVATE-SECRET\n",1))
    result=simulate(snapshot,query(snapshot))
    assert result["status"]=="ok" and result["total_cost"]==30
    warning=next(d for d in result["diagnostics"] if d["code"]=="unmodeled_authentication")
    assert warning["ignored"] and "认证与邻接正常" in warning["message"]
    assert "PRIVATE-SECRET" not in str(snapshot["routing"]) and "PRIVATE-SECRET" not in str(result)


@pytest.mark.parametrize("replacement,code",[
    ("isis metric invalid level-2","invalid_cost"),
    ("isis network multipoint","unsupported_network_type"),
])
def test_generic_interface_fallback_cannot_invent_metric_or_network_type(replacement,code):
    old="isis metric 10 level-2" if code=="invalid_cost" else "isis network point-to-point"
    snapshot=snapshot_with("01-S-cisco-ios.cfg",lambda s:s.replace(old,replacement,1))
    result=simulate(snapshot,query(snapshot,"isis","2"))
    assert result["status"]=="incomplete" and not result["paths"]
    assert any(d["code"]==code and not d.get("ignored") for d in result["diagnostics"])


def test_advanced_spf_and_lsp_timers_do_not_block_static_basic_paths():
    snapshot=snapshot_with("01-S-cisco-ios.cfg",lambda s:s.replace("router isis CORE\n","router isis CORE\n spf-interval 50 100 1000\n lsp-gen-interval 5\n"))
    result=simulate(snapshot,query(snapshot,"isis","2"))
    assert result["status"]=="ok" and result["total_cost"]==30
    assert len([d for d in result["diagnostics"] if d.get("ignored")])==2


@pytest.mark.parametrize("file,transform",[
    ("01-S-cisco-ios.cfg",lambda s:s.replace("router isis CORE\n","router isis CORE\n shutdown\n")),
    ("06-D-huawei.cfg",lambda s:s.replace(" isis enable 6\n"," isis enable 6\n undo isis enable 6\n")),
])
def test_unknown_explicit_activation_state_cannot_be_ignored_as_an_extension(file,transform):
    snapshot=snapshot_with(file,transform)
    result=simulate(snapshot,query(snapshot,"isis","2"))
    assert result["status"]=="incomplete" and not result["paths"]
    assert any(d["code"]=="unresolved_protocol_state" and not d.get("ignored") for d in result["diagnostics"])


@pytest.mark.parametrize("explicit",[False,True])
def test_junos_automatic_bandwidth_cost_does_not_fall_back_to_ten(explicit):
    def transform(text):
        text+="\nset protocols isis reference-bandwidth 10g\n"
        if not explicit:
            text=text.replace("set protocols isis interface ge-0/0/0.0 level 2 metric 10\n","")
            text=text.replace("set protocols isis interface ge-0/0/1.0 level 2 metric 20\n","")
        return text
    snapshot=snapshot_with("02-A-juniper-set.cfg",transform)
    request=query(snapshot,"isis","2")
    result=simulate(snapshot,request)
    if explicit:
        assert result["status"]=="ok" and result["total_cost"]==30
    else:
        unknown=[b for b in snapshot["routing"]["bindings"] if b["device_id"]=="02-A-juniper-set" and b["scope"]=="2" and not b["passive"]]
        assert unknown and all(b["cost"] is None and b["cost_origin"]=="unknown" for b in unknown)
        assert result["status"]=="incomplete" and any(d["code"]=="unknown_cost" for d in result["diagnostics"])
        request.scenario.unknown_cost=10
        assert simulate(snapshot,request)["status"]=="ok"


def test_known_hello_dead_mismatch_still_removes_candidate_adjacencies():
    snapshot,request=lab()
    for binding in snapshot["routing"]["bindings"]:
        if binding["instance_id"]==request.source_instance_id:
            binding["hello"],binding["dead"]=11,44
    result=simulate(snapshot,request)
    assert result["status"]=="unreachable" and not result["paths"]
    assert any(d["code"]=="incompatible_adjacency" and not d.get("ignored") for d in result["diagnostics"])
