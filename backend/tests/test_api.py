from copy import deepcopy
import csv
import io
import json
import xml.etree.ElementTree as ET
import zipfile

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.store import Job, Project, Store


def config(hostname, address, vrf=None):
    return f"version 15.2\nhostname {hostname}\nusername admin secret PRIVATE-SECRET-MUST-NOT-LEAK\ninterface GigabitEthernet0/0\n description backbone\n{f' ip vrf forwarding {vrf}' if vrf else ''}\n ip address {address} 255.255.255.252\n no shutdown\n!\nend\n".encode()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as session:
        yield session


def project(client, name="Integration lab"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, p, files=None, strategy="merge"):
    files = files or [("a.cfg", config("A", "10.0.0.1")), ("b.cfg", config("B", "10.0.0.2"))]
    response = client.post(f"/api/projects/{p['id']}/imports", files=[("files", (name, data, "application/octet-stream")) for name, data in files], data={"expected_version": str(p["version"]), "strategy": strategy})
    assert response.status_code == 202, response.text
    return response.json(), client.get(f"/api/projects/{p['id']}").json()


def edit(client, p, operation, target=None, value=None):
    response = client.post(f"/api/projects/{p['id']}/overrides", json={"version": p["version"], "operation": operation, "target": target or {}, "value": value or {}, "reason": "Integration review"})
    assert response.status_code == 200, response.text
    return response.json()


def restore(client, bundle):
    response = client.post("/api/projects/restore", files={"file": ("project.iptopo.json", json.dumps(bundle).encode(), "application/json")})
    assert response.status_code == 201, response.text
    return response.json()


def test_health_capabilities_import_evidence_and_source_on_demand(client):
    assert client.get("/api/health").json()["status"] == "ok"
    capabilities = client.get("/api/capabilities").json()
    assert len(capabilities["dialects"]) >= 8
    job, p = upload(client, project(client))
    assert job["status"] == "completed"
    assert job["progress"] == 100
    assert len(p["snapshot"]["devices"]) == 2
    assert len(p["topology"]["edges"]) == 1
    assert p["topology"]["edges"][0]["review_state"] == "pending"
    assert "PRIVATE-SECRET" not in json.dumps(p)
    source_id = p["snapshot"]["source_files"][0]["id"]
    source = client.get(f"/api/projects/{p['id']}/sources/{source_id}")
    assert source.status_code == 200
    assert "PRIVATE-SECRET" in source.json()["text"]
    other = project(client)
    assert client.get(f"/api/projects/{other['id']}/sources/{source_id}").status_code == 404
    assert client.get(f"/api/imports/{job['id']}").json()["status"] == "completed"


def test_at_18_failed_file_isolated_good_file_and_old_facts_survive(client):
    job, p = upload(client, project(client), [("broken.cfg", b"not a supported device config"), ("a.cfg", config("A", "10.0.0.1"))])
    assert job["status"] == "completed"
    assert len(p["snapshot"]["devices"]) == 1
    assert {row["status"] for row in job["results"]} >= {"failed"}
    old_snapshot = p["snapshot_id"]
    job, unchanged = upload(client, p, [("a.cfg", b"not a config")])
    assert job["status"] == "failed"
    assert unchanged["snapshot_id"] == old_snapshot
    assert unchanged["version"] == p["version"]


def test_failed_replacement_does_not_drop_preserved_device_in_later_merge(client):
    _, p = upload(client, project(client), [("a.cfg", config("A", "10.0.0.1"))])
    _, p = upload(client, p, [("a.cfg", b"not a supported config"), ("b.cfg", config("B", "10.0.0.2"))])
    assert {d["hostname"] for d in p["snapshot"]["devices"]} == {"A", "B"}
    _, p = upload(client, p, [("c.cfg", config("C", "10.0.1.1"))])
    assert {d["hostname"] for d in p["snapshot"]["devices"]} == {"A", "B", "C"}


def test_optimistic_versions_reject_stale_mutations(client):
    original = project(client)
    _, current = upload(client, original)
    path = f"/api/projects/{current['id']}"
    assert client.post(path + "/infer", json={"version": original["version"]}).status_code == 409
    assert client.post(path + "/overrides", json={"version": original["version"], "operation": "add_device", "value": {"hostname": "bad"}}).status_code == 409
    response = client.post(path + "/imports", files={"files": ("stale.cfg", config("Stale", "10.0.1.1"))}, data={"expected_version": original["version"]})
    assert response.status_code == 409
    assert client.get(path).json()["version"] == current["version"]


def test_at_15_suppress_reimport_and_changed_premise_conflict(client):
    _, p = upload(client, project(client))
    link_id = p["snapshot"]["links"][0]["id"]
    p = edit(client, p, "suppress_link", {"link_id": link_id})
    assert not p["topology"]["edges"]
    assert p["snapshot"]["links"][0]["review_state"] == "rejected"
    _, p = upload(client, p)
    assert p["overrides"][0]["status"] == "active"
    assert not p["topology"]["edges"]
    _, p = upload(client, p, [("a.cfg", config("A", "10.0.1.1")), ("b.cfg", config("B", "10.0.1.2"))])
    assert p["overrides"][0]["status"] == "conflict"
    assert p["snapshot"]["links"][0]["id"] == link_id
    assert len(p["topology"]["edges"]) == 1
    assert p["topology"]["edges"][0]["review_state"] == "pending"


def test_at_16_rebind_removed_endpoint_conflicts_after_replace(client):
    _, p = upload(client, project(client), [("a.cfg", config("A", "10.0.0.1")), ("b.cfg", config("B", "10.0.0.2")), ("c.cfg", config("C", "10.0.1.1"))])
    c_device = next(d["id"] for d in p["snapshot"]["devices"] if d["hostname"] == "C")
    c_interface = next(i["id"] for i in p["snapshot"]["interfaces"] if i["device_id"] == c_device)
    p = edit(client, p, "rebind_endpoint", {"link_id": p["snapshot"]["links"][0]["id"]}, {"endpoint": "b", "interface_id": c_interface})
    assert p["overrides"][0]["status"] == "active"
    _, p = upload(client, p, strategy="replace")
    assert p["overrides"][0]["status"] == "conflict"
    assert all(e["origin"] == "inferred" for e in p["topology"]["edges"])


def test_at_17_views_reopen_persist_and_reimport_keeps_positions(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, p = upload(client, project(client))
        node = p["topology"]["nodes"][0]["id"]
        view = {"id": "view-office", "name": "Office", "positions": {node: {"x": 125.5, "y": 240}}, "pinned": [node], "zoom": 1.5, "pan": {"x": 20, "y": -8}}
        response = client.put(f"/api/projects/{p['id']}/views/view-office", json={"version": p["version"], "view": view})
        assert response.status_code == 200, response.text
        p = response.json()
        _, p = upload(client, p)
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        reopened = client.get(f"/api/projects/{p['id']}").json()
        saved = reopened["views"][0]
        assert saved["positions"] == view["positions"]
        assert saved["pinned"] == [node]
        assert saved["pan"] == view["pan"]
        assert node in {n["id"] for n in reopened["topology"]["nodes"]}


def test_at_19_same_hostname_keeps_independent_identity(client):
    _, p = upload(client, project(client), [("one.cfg", config("SharedName", "10.0.0.1")), ("two.cfg", config("SharedName", "10.0.0.2"))])
    assert len(p["snapshot"]["devices"]) == 2
    assert len({d["id"] for d in p["snapshot"]["devices"]}) == 2
    assert any(d["code"] == "duplicate_hostname" for d in p["snapshot"]["diagnostics"])
    assert len(p["topology"]["edges"]) == 1


@pytest.mark.parametrize("include_sources", [False, True])
def test_at_20_json_restore_preserves_overrides_views_and_confidentiality(client, include_sources):
    _, p = upload(client, project(client))
    p = edit(client, p, "confirm_link", {"link_id": p["snapshot"]["links"][0]["id"]})
    exported = client.get(f"/api/projects/{p['id']}/exports/json", params={"include_sources": str(include_sources).lower()})
    assert exported.status_code == 200
    bundle = exported.json()
    assert bool(bundle["sources"]) == include_sources
    assert ("PRIVATE-SECRET" in exported.text) == include_sources
    recovered = restore(client, bundle)
    assert recovered["id"] != p["id"]
    assert recovered["snapshot_id"] != p["snapshot_id"]
    assert recovered["snapshot"]["devices"] == p["snapshot"]["devices"]
    assert recovered["overrides"][0]["status"] == "active"
    assert recovered["topology"]["edges"][0]["review_state"] == "confirmed"
    source_id = recovered["snapshot"]["source_files"][0]["id"]
    response = client.get(f"/api/projects/{recovered['id']}/sources/{source_id}")
    assert response.status_code == (200 if include_sources else 404)


def test_user_notes_survive_api_and_source_free_backup(client):
    _, p = upload(client, project(client))
    device_id = p["snapshot"]["devices"][0]["id"]
    p = edit(client, p, "add_note", {"kind": "device", "id": device_id}, {"text": "This CE is operated by the branch team"})
    assert p["snapshot"]["devices"][0]["notes"][0]["text"] == "This CE is operated by the branch team"
    bundle = client.get(f"/api/projects/{p['id']}/exports/json").json()
    assert bundle["project"]["overrides"][0]["value"]["text"] == "This CE is operated by the branch team"
    recovered = restore(client, bundle)
    assert recovered["snapshot"]["devices"][0]["notes"][0]["text"] == "This CE is operated by the branch team"


def test_csv_and_drawio_match_final_graph_and_saved_coordinates(client):
    _, p = upload(client, project(client))
    node = p["topology"]["nodes"][0]["id"]
    response = client.put(f"/api/projects/{p['id']}/views/v1", json={"version": p["version"], "view": {"id": "v1", "name": "Export layout", "positions": {node: {"x": 420, "y": 260}}}})
    assert response.status_code == 200, response.text
    p = response.json()
    response = client.get(f"/api/projects/{p['id']}/exports/csv?view_id=v1")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"devices.csv", "interfaces.csv", "addresses.csv", "segments.csv", "links.csv"}
        rows = list(csv.DictReader(io.StringIO(archive.read("links.csv").decode("utf-8-sig"))))
        assert len(rows) == len(p["topology"]["edges"])
        assert len(list(csv.DictReader(io.StringIO(archive.read("devices.csv").decode("utf-8-sig"))))) == len(p["topology"]["nodes"])
    response = client.get(f"/api/projects/{p['id']}/exports/drawio?view_id=v1")
    assert response.status_code == 200, response.text
    xml = ET.fromstring(response.content)
    assert len(xml.findall(".//object/mxCell[@vertex='1']")) == len(p["topology"]["nodes"])
    assert len(xml.findall(".//mxCell[@edge='1']")) == len(p["topology"]["edges"])
    obj = xml.find(f".//object[@id='{node}']")
    assert obj is not None
    geometry = obj.find("mxCell/mxGeometry")
    assert float(geometry.get("x")) == 350
    assert float(geometry.get("y")) == 230


def test_domain_mapping_and_confirmed_default_assumption(client):
    _, p = upload(client, project(client), [("pe.cfg", config("PE", "10.0.0.1", "Customer")), ("ce.cfg", config("CE", "10.0.0.2"))])
    assert not p["topology"]["edges"]
    context_ids = [c["id"] for c in p["snapshot"]["contexts"]]
    domains = {"domains": [{"id": "customer", "name": "Customer", "confirmed": True}], "mappings": {c: "customer" for c in context_ids}, "interface_mappings": {}, "segment_splits": {}}
    response = client.put(f"/api/projects/{p['id']}/domains", json={"version": p["version"], "domains": domains})
    assert response.status_code == 200, response.text
    p = response.json()
    assert len(p["topology"]["edges"]) == 1
    assert p["topology"]["edges"][0]["review_state"] == "pending"
    assert p["topology"]["edges"][0]["evidence"][0]["domain_id"] == "customer"


def test_demo_and_historical_snapshot_ownership(client):
    p = project(client)
    response = client.post(f"/api/projects/{p['id']}/demo")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "completed"
    p = client.get(f"/api/projects/{p['id']}").json()
    assert len(p["snapshot"]["devices"]) >= 4
    historical = client.get(f"/api/projects/{p['id']}/snapshots/{p['snapshot_id']}")
    assert historical.status_code == 200
    other = project(client)
    assert client.get(f"/api/projects/{other['id']}/snapshots/{p['snapshot_id']}").status_code == 404


def test_invalid_restore_rejected_without_project_creation(client):
    before = len(client.get("/api/projects").json())
    response = client.post("/api/projects/restore", files={"file": ("bad.json", b'{"schema_version":900}', "application/json")})
    assert response.status_code == 422
    assert len(client.get("/api/projects").json()) == before


def test_interrupted_jobs_recovered_on_restart(tmp_path):
    app = create_app(tmp_path, worker_mode="inline")
    with TestClient(app) as client:
        p = project(client)
        with app.state.store.session(write=True) as session:
            session.add(Job(id="job-interrupted", project_id=p["id"], status="running", progress=42, data={"errors": []}))
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        job = client.get("/api/imports/job-interrupted").json()
        assert job["status"] == "interrupted"
        assert job["progress"] == 42
        assert job["errors"]


@pytest.mark.parametrize("overrides", [[7], [{"operation": "force_link", "value": [], "target": {}}]])
def test_invalid_restored_override_is_rejected_before_committing(client, overrides):
    _, p = upload(client, project(client))
    bundle = client.get(f"/api/projects/{p['id']}/exports/json").json()
    bundle["project"]["overrides"] = overrides
    before = len(client.get("/api/projects").json())
    response = client.post("/api/projects/restore", files={"file": ("invalid.iptopo.json", json.dumps(bundle), "application/json")})
    assert response.status_code == 422
    assert len(client.get("/api/projects").json()) == before


def test_malformed_domain_split_is_rejected_without_poisoning_project(client):
    _, p = upload(client, project(client))
    domains = deepcopy(p["domains"])
    domains["segment_splits"] = ["invalid"]
    response = client.put(f"/api/projects/{p['id']}/domains", json={"version": p["version"], "domains": domains})
    assert response.status_code == 422
    assert client.get(f"/api/projects/{p['id']}").json()["version"] == p["version"]
