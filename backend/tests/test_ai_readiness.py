"""Regressions for facts/paths that are now exposed to Agents."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.overrides import apply_overrides
from backend.app.parser import parse_config
from backend.app.routing.simulation import simulate
from backend.tests.test_api import config, project, upload
from backend.tests.test_lsdb import batch, query
from backend.tests.test_routing import lab


def test_replacement_preserves_old_facts_after_input_read_failure(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, p = upload(client, project(client))
        job, updated = upload(client, p, [("a.cfg", b"\x00broken"), ("b.cfg", config("B", "10.0.0.2"))], strategy="replace")
        assert job["status"] == "completed"
        assert {row["hostname"] for row in updated["snapshot"]["devices"]} == {"A", "B"}
        assert any("保留" in row.get("message", "") for row in job["results"])


@pytest.mark.parametrize("protocol", ["ospf", "isis"])
@pytest.mark.parametrize("purge", [False, True])
def test_latest_summary_cannot_be_replaced_by_older_detailed_body(protocol, purge):
    observation = batch(protocol)
    newer = deepcopy(observation.records[1])
    newer.sequence += 1
    newer.detailed = False
    newer.links, newer.prefixes = [], []
    if purge:
        newer.expired = True
        newer.age = 3600 if protocol == "ospf" else 0
    observation.records.append(newer)
    result = query(observation)["simulation"]
    assert result["status"] == "incomplete"
    assert result["paths"] == []
    assert any(row["code"] in {"missing_record", "summary_only"} for row in result["diagnostics"])


def test_observed_spf_is_unchanged_by_configured_narrow_metric_profile():
    observation = batch("isis")
    snapshot, _ = lab("isis", "2")
    for record in observation.records:
        for link in record.links:
            link.cost = 600
    for instance in snapshot["routing"]["instances"]:
        if instance["protocol"] == "isis":
            instance["metric_style"] = "narrow"
    observed = query(observation, snapshot, mode="observed")["simulation"]
    fused = query(observation, snapshot, mode="fused")["simulation"]
    assert observed["status"] == fused["status"] == "ok"
    assert observed["total_cost"] == fused["total_cost"] == 1200
    assert observed["paths"] == fused["paths"]


def test_manual_replacement_preserves_the_same_protocol_paths():
    snapshot, request = lab()
    link = next(row for row in snapshot["links"] if {row["device_a_id"], row["device_b_id"]} == {"01-S", "02-A"})
    edits = [dict(id="reject", operation="suppress_link", target={"link_id":link["id"]}, value={}, status="active"),
             dict(id="manual", operation="force_link", target={}, status="active",
                  value={"interface_a_id":link["interface_a_id"], "interface_b_id":link["interface_b_id"]})]
    corrected, evaluated = apply_overrides(snapshot, edits)
    before, after = simulate(snapshot, request), simulate(corrected, request)
    assert all(row["status"] == "active" for row in evaluated)
    assert before["path_count"] == after["path_count"] == "3"
    assert before["total_cost"] == after["total_cost"] == 30


def test_pagination_marker_survives_normalization_as_a_diagnostic():
    from backend.tests.test_routing import SAMPLES
    parsed = parse_config((SAMPLES/"01-S.cfg").read_text(encoding="utf-8")+"\n---- More ----\n", "a.cfg", "source", "device")
    assert parsed["status"] == "partial"
    assert any(row["code"] == "truncated_configuration" for row in parsed["diagnostics"])
    assert any(row["code"] == "incomplete_configuration" for row in parsed["routing"]["issues"])


def test_lsdb_import_changes_fact_revision_without_changing_config_snapshot(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        p = project(client)
        old_revision = p["effective_revision"]
        result = client.post(f"/api/projects/{p['id']}/lsdb/demo", json={"version":p["version"]})
        assert result.status_code == 201, result.text
        new = client.get(f"/api/projects/{p['id']}").json()
        assert new["snapshot_id"] == p["snapshot_id"]
        assert new["effective_revision"] != old_revision
