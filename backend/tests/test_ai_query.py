"""Agent queries preserve deterministic facts, scope, evidence and resource bounds."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from backend.app.ai.query import QueryEngine, _path_view
from backend.tests.test_lsdb import batch
from backend.tests.test_routing import lab


@pytest.fixture
def engine():
    snapshot, _ = lab()
    return QueryEngine(snapshot, project_id="project-a", revision="frozen-r1", project_version=7,
                       batches=[batch().model_dump(mode="json")])


def test_summary_is_frozen_project_scoped_and_backend_counted():
    snapshot, _ = lab()
    query = QueryEngine(snapshot, project_id="project-a", revision="r1", project_version=7)
    snapshot["devices"].clear()
    result = query.execute("network_summary", {})
    assert result["status"] == "ok"
    assert result["data"]["device_count"] == 6
    assert result["context"] == {"project_id": "project-a", "snapshot_id": "lab", "revision": "r1", "project_version": 7}
    assert result["cards"][0]["kind"] == "summary"
    result["data"]["device_count"] = 999
    assert query.execute("network_summary", {})["data"]["device_count"] == 6
    with pytest.raises(ValueError):
        QueryEngine({"project_id": "project-b"}, project_id="project-a", revision="r1")


def test_entity_selection_accepts_only_frozen_project_identifiers(engine):
    assert engine.contains_entity("01-S")
    interface_id = engine.execute("device_details", {"device_id": "01-S"})["data"]["interfaces"][0]["id"]
    assert engine.contains_entity(interface_id)
    link_id = engine.execute("query_topology", {})["data"]["edges"][0]["id"]
    assert engine.contains_entity(link_id)
    assert not engine.contains_entity("foreign-project-device")
    assert not engine.contains_entity(None)


def test_search_pagination_ip_candidates_details_and_evidence(engine):
    result = engine.execute("search_devices", {"query": "IGP", "limit": 2})
    assert result["data"]["total"] == 6
    assert len(result["data"]["devices"]) == 2
    assert result["data"]["next_offset"] == 2
    second = engine.execute("search_devices", {"query": "IGP", "offset": 2, "limit": 2})
    assert not set(x["id"] for x in result["data"]["devices"]) & set(x["id"] for x in second["data"]["devices"])
    found = engine.execute("search_devices", {"query": "10.255.0.1"})
    assert found["data"]["devices"][0]["hostname"] == "IGP-S"
    details = engine.execute("device_details", {"device_id": "01-S", "limit": 1})
    assert details["data"]["device"]["hostname"] == "IGP-S"
    assert len(details["data"]["interfaces"]) == 1
    assert details["data"]["next_offset"] == 1
    assert details["evidence"]
    assert "text" not in details["evidence"][0]
    assert details["cards"][0]["device_ids"] == ["01-S"]


@pytest.mark.parametrize("name,args,code", [
    ("shell", {"command": "id"}, "unknown_tool"),
    ("search_devices", {"project_id": "project-b"}, "invalid_arguments"),
    ("search_devices", {"limit": 101}, "invalid_arguments"),
    ("search_devices", {"offset": -1}, "invalid_arguments"),
    ("query_topology", {"hops": 4}, "invalid_arguments"),
    ("device_details", {"device_id": "foreign-project-device"}, "device_not_found"),
    ("query_topology", {"segment_id": "missing"}, "segment_not_found"),
    ("get_evidence", {"source_file_id": "/etc/passwd"}, "source_not_found"),
    ("lsdb_path", {"batch_id": "other-project-batch"}, "batch_not_found"),
])
def test_invalid_tools_arguments_and_foreign_ids_are_not_executed(engine, name, args, code):
    result = engine.execute(name, args)
    assert result["status"] == "error"
    assert result["diagnostics"][0]["code"] == code
    assert result["context"]["project_id"] == "project-a"


def test_empty_project_is_incomplete_not_unreachable():
    query = QueryEngine({}, project_id="empty", revision="r1")
    assert query.execute("network_summary", {})["status"] == "incomplete"
    assert query.execute("list_path_targets", {})["status"] == "incomplete"
    result = query.execute("config_path", {"protocol": "ospf", "scope": "0.0.0.0", "source_instance_id": "none", "destination_advertisement_id": "none"})
    assert result["status"] == "incomplete"
    assert result["data"]["hops"] == []
    assert "highlight" not in result["cards"][0]


def test_topology_is_paginated_and_does_not_claim_live_state(engine):
    result = engine.execute("query_topology", {"device_id": "01-S", "limit": 1})
    assert len(result["data"]["edges"]) == 1
    assert result["data"]["next_offset"] == 1
    assert result["cards"][0]["link_ids"]
    assert result["data"]["layer"] == "config"
    assert result["assumptions"]


def path_args(query):
    targets = query.execute("list_path_targets", {"protocol": "ospf"})["data"]
    source = next(i for i in targets["instances"] if i["hostname"] == "IGP-S")
    destination = next(a for a in targets["advertisements"] if a["hostname"] == "IGP-D")
    return {"protocol": "ospf", "scope": "0.0.0.0", "source_instance_id": source["id"], "destination_advertisement_id": destination["id"]}


def test_config_path_card_uses_engine_costs_and_temporary_scenarios(engine):
    args = path_args(engine)
    before = engine.execute("config_path", args)
    assert before["status"] == "ok"
    assert before["data"]["total_cost"] == 30
    assert before["data"]["path_count"] == "3"
    assert sorted(p["share_percent"] for p in before["data"]["paths"]) == [25, 25, 50]
    card = before["cards"][0]
    assert card["kind"] == "path"
    assert card["highlight"]["source_device_id"] == "01-S"
    assert card["highlight"]["hops"] == before["data"]["hops"]
    assert card["link_ids"] and card["evidence"]
    transit = next(h for h in before["data"]["hops"] if h["source_device_id"] == "01-S")
    changed = engine.execute("config_path", {**args, "scenario": {"disabled_interfaces": [transit["interface_id"]]}})
    assert changed["data"]["path_count"] != "3"
    assert engine.execute("config_path", args)["data"]["path_count"] == "3"
    assert engine.execute("config_path", {**args, "source_instance_id": "foreign"})["status"] == "error"
    assert engine.execute("config_path", {**args, "scenario": {"unknown_cost": True}})["status"] == "error"


def lsdb_args(query, batch_id="observation"):
    targets = query.execute("list_path_targets", {"batch_id": batch_id})["data"]
    src = next(n for n in targets["nodes"] if n["protocol_id"] == "10.255.0.1")
    dst = next(p for p in targets["destinations"] if p["prefix"] == "10.255.0.6/32")
    return {"batch_id": batch_id, "source": src["id"], "destination": dst["id"]}


def test_lsdb_paths_and_comparison_keep_batch_time_and_graph_layer(engine):
    args = lsdb_args(engine)
    assert engine.contains_lsdb_entity(args["source"], "observation")
    assert not engine.contains_lsdb_entity(args["source"], "other-project-batch")
    assert not engine.contains_lsdb_entity("foreign-node", "observation")
    result = engine.execute("lsdb_path", args)
    assert result["status"] == "ok" and result["data"]["total_cost"] == 30
    assert result["context"]["lsdb_batch_id"] == "observation"
    assert result["context"]["collected_at"] is None
    assert result["data"]["layer"] == "lsdb"
    assert result["cards"][0]["highlight"]
    assert result["data"]["topology"]["edges"]
    comparison = engine.execute("compare", {**args, "limit": 2})
    assert comparison["status"] == "ok"
    assert len(comparison["data"]["comparisons"]) == 2
    assert comparison["data"]["config_simulation"]["total_cost"] == 30


def test_incomplete_lsdb_and_wrong_ids_are_not_optimistic_paths():
    observation = batch(declared_complete=False).model_dump(mode="json")
    query = QueryEngine(lab()[0], project_id="p", revision="r", batches=[observation])
    args = lsdb_args(query)
    result = query.execute("lsdb_path", args)
    assert result["status"] == "incomplete" and not result["data"]["paths"]
    assert "highlight" not in result["cards"][0]
    assert query.execute("lsdb_path", {**args, "source": "foreign-node"})["status"] == "error"
    assert query.execute("lsdb_path", {"batch_id": "observation", "source": args["source"]})["status"] == "error"


def test_batch_and_source_loaders_only_receive_whitelisted_ids_and_freeze_results():
    snapshot, _ = lab()
    full = batch().model_dump(mode="json")
    metadata = {k: v for k, v in full.items() if k not in {"records", "diagnostics"}}
    calls = []
    def loader(batch_id):
        calls.append(batch_id)
        return full
    query = QueryEngine(snapshot, project_id="p", revision="r", batches=[metadata], batch_loader=loader)
    assert query.execute("lsdb_path", {"batch_id": "other"})["status"] == "error" and calls == []
    assert query.execute("lsdb_path", {"batch_id": full["id"]})["status"] == "ok"
    full["records"].clear()
    assert query.execute("list_path_targets", {"batch_id": full["id"]})["data"]["nodes"]
    assert calls == [full["id"]]


def test_source_snippets_redact_credentials_and_private_key_continuations():
    text = "hostname R1\nusername admin secret TOPSECRET\nsnmp-server community PRIVATECOMM RO\n-----BEGIN PRIVATE KEY-----\nVERYPRIVATEKEYMATERIAL\n-----END PRIVATE KEY-----\ninterface Gi0/0\n ip address 10.0.0.1 255.255.255.0\n"
    calls = []
    def source_loader(source_id):
        calls.append(source_id)
        return {"filename": "r1.cfg", "text": text}
    query = QueryEngine({"source_files": [{"id": "source", "filename": "r1.cfg", "hash": "original-upload-hash"}]}, project_id="p", revision="r", source_loader=source_loader)
    assert query.execute("get_evidence", {"source_file_id": "foreign"})["status"] == "error" and not calls
    result = query.execute("get_evidence", {"source_file_id": "source", "line": 5, "end_line": 8})
    encoded = json.dumps(result)
    assert "VERYPRIVATEKEYMATERIAL" not in encoded and "TOPSECRET" not in encoded
    assert "10.0.0.1" in encoded and "REDACTED" in encoded
    assert result["evidence"][0]["filename"] == "r1.cfg"
    assert result["evidence"][0]["source_sha256"] == "original-upload-hash"
    assert len(result["evidence"][0]["text_sha256"]) == 64
    assert query.execute("get_evidence", {"source_file_id": "source", "line": 1, "end_line": 101})["status"] == "error"
    assert query.execute("get_evidence", {"source_file_id": "source", "line": 99})["status"] == "error"


def test_cards_are_deduplicated_and_tool_trace_is_thread_safe(engine):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: engine.execute("network_summary", {}), range(16)))
    assert len(engine.cards) == 1
    assert len(engine.trace) == 16
    assert len({r["cards"][0]["id"] for r in results}) == 1
    assert all(t["name"] == "network_summary" and t["status"] == "ok" for t in engine.trace)


def test_catalog_does_not_expose_arbitrary_execution_or_cross_project_arguments(engine):
    tools = {t["name"]: t for t in engine.tool_catalog()}
    assert len(tools) == 9
    for tool in tools.values():
        assert tool["annotations"]["readOnlyHint"]
        assert tool["inputSchema"]["additionalProperties"] is False
        assert "project_id" not in tool["inputSchema"].get("properties", {})


def test_large_path_presentation_is_bounded_without_changing_spf_numbers():
    full = {"status": "ok", "total_cost": 123, "path_count": "999999", "paths": [],
            "hops": [{"id": str(n)} for n in range(5000)], "forwarding": [{"device_id": str(n)} for n in range(5000)]}
    bounded = _path_view(full)
    assert bounded["total_cost"] == 123 and bounded["path_count"] == "999999"
    assert bounded["hops_total"] == 5000 and bounded["hops_truncated"]
    assert len(bounded["hops"]) == len(bounded["forwarding"]) == 512
    assert bounded["diagnostics"][-1]["code"] == "result_truncated"
    assert len(full["hops"]) == 5000


def test_accumulated_cards_are_redacted_too():
    query = QueryEngine({"devices": [{"id": "a", "hostname": "password TOPSECRET"}]}, project_id="p", revision="r")
    query.execute("device_details", {"device_id": "a"})
    assert "TOPSECRET" not in json.dumps(query.cards)


def test_truncated_diagnostics_do_not_hide_later_completeness_blockers():
    observed = batch()
    from backend.app.lsdb.model import Diagnostic
    observed.diagnostics = [Diagnostic(code="notice", message="informational", blocking=False) for _ in range(101)]
    observed.diagnostics.append(Diagnostic(code="missing", message="capture incomplete", blocking=True))
    query = QueryEngine(lab()[0], project_id="p", revision="r", batches=[observed])
    result = query.execute("lsdb_path", {"batch_id": observed.id})
    assert result["status"] == "incomplete"
    assert len(result["diagnostics"]) == 100


def test_saved_lsdb_mappings_are_frozen_and_only_affect_fused_comparison():
    snapshot, _ = lab()
    instance = next(i for i in snapshot["routing"]["instances"] if i["device_id"] == "01-S" and i["protocol"] == "ospf")
    duplicate = {**deepcopy(instance), "id": "ambiguous-instance"}
    snapshot["routing"]["instances"].append(duplicate)
    mappings = {"observation": {instance["router_id"]: instance["id"]}}
    query = QueryEngine(snapshot, project_id="p", revision="r", batches=[batch()], mappings=mappings)
    mappings["observation"][instance["router_id"]] = "foreign-instance"
    fused = query.execute("compare", {"batch_id": "observation"})
    mapped = next(n for n in fused["data"]["topology"]["nodes"] if n["protocol_id"] == instance["router_id"])
    assert mapped["mapping_status"] == "matched" and mapped["instance_id"] == instance["id"]
    observed = query.execute("lsdb_path", {"batch_id": "observation"})
    node = next(n for n in observed["data"]["topology"]["nodes"] if n["protocol_id"] == instance["router_id"])
    assert node["mapping_status"] == "lsdb_only" and node["instance_id"] is None


def test_stale_saved_mapping_has_explicit_nonblocking_fallback():
    query = QueryEngine(lab()[0], project_id="p", revision="r", batches=[batch()],
                        mappings={"observation": {"10.255.0.1": "removed-instance"}})
    args = lsdb_args(query)
    compared = query.execute("compare", args)
    assert compared["status"] == "ok" and compared["data"]["total_cost"] == 30
    assert any(d["code"] == "stale_mapping" for d in compared["diagnostics"])
    assert compared["data"]["config_simulation"]["total_cost"] == 30


def test_evidence_redacts_generic_tacacs_keys_and_snmp_user_authentication():
    raw = "tacacs-server host 10.0.0.2 key 7 TACACSKEY\nsnmp-server user monitor admin v3 auth sha AUTHKEY priv aes 128 PRIVKEY\nAuthorization: Bearer PRIVATEBEARER\ninterface GigabitEthernet0/0\n"
    query = QueryEngine({"source_files": [{"id": "s", "filename": "r.cfg"}]}, project_id="p", revision="r", source_loader=lambda _: {"text": raw})
    result = query.execute("get_evidence", {"source_file_id": "s"})
    encoded = json.dumps(result)
    for value in ("TACACSKEY", "AUTHKEY", "PRIVKEY", "PRIVATEBEARER"):
        assert value not in encoded
    assert "GigabitEthernet0/0" in encoded
