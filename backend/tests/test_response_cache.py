"""Views and summary reads must stay independent from expensive fact replay."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main


def test_view_summary_preserves_facts_and_cached_reads_invalidate_on_edits(tmp_path):
    with TestClient(main.create_app(tmp_path, worker_mode="inline")) as client:
        p = client.post("/api/projects", json={"name": "Cache lab"}).json()
        path = f"/api/projects/{p['id']}"
        p = client.post(path + "/overrides", json={"version": p["version"], "operation": "add_device", "value": {"hostname": "A"}}).json()
        original_topology = p["topology"]
        original_snapshot = p["snapshot"]
        assert client.get("/api/projects").json()[0]["device_count"] == 1
        with patch.object(main, "apply_overrides", wraps=main.apply_overrides) as replay:
            response = client.put(path + "/views/main?summary=true", json={"version": p["version"], "view": {"id": "main", "name": "Main", "positions": {original_topology['nodes'][0]['id']: {"x": 50, "y": 60}}}})
            assert response.status_code == 200
            saved = response.json()
            assert set(saved) == {"id", "version", "updated_at", "views"}
            assert saved["version"] == p["version"] + 1
            assert client.get(path).json()["snapshot"] == original_snapshot
            assert client.get(path).json()["topology"] == original_topology
            assert replay.call_count == 0
            stale = client.put(path + "/views/main?summary=true", json={"version": p["version"], "view": {"id": "main", "name": "Stale"}})
            assert stale.status_code == 409
            edited = client.post(path + "/overrides", json={"version": saved["version"], "operation": "override_field", "target": {"kind": "device", "id": original_topology['nodes'][0]['id'], "field": "hostname"}, "value": {"value": "B"}}).json()
            assert edited["topology"]["nodes"][0]["label"] == "B"
            assert replay.call_count > 0
            assert original_topology["nodes"][0]["label"] == "A"
        added = next(r for r in edited["overrides"] if r["operation"] == "add_device")
        response = client.patch(path + f"/overrides/{added['id']}", json={"version": edited["version"], "status": "undone"})
        assert response.status_code == 200
        assert client.get("/api/projects").json()[0]["device_count"] == 0


def test_history_and_project_lists_read_metadata_and_jobs_expose_details_separately(tmp_path):
    with TestClient(main.create_app(tmp_path, worker_mode="inline")) as client:
        p = client.post("/api/projects", json={"name": "History lab"}).json()
        path = f"/api/projects/{p['id']}"
        text = b"version 15.2\nhostname A\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.252\n!\n"
        response = client.post(path + "/imports", files={"files": ("a.cfg", text)}, data={"expected_version": p["version"]})
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "completed"
        p = client.get(path).json()
        assert p["jobs"][0]["results"] == []
        assert client.get(f"/api/imports/{job['id']}").json()["results"]
        with patch.object(main, "apply_overrides", side_effect=AssertionError("Metadata read replayed facts")):
            history = client.get(path + "/snapshots").json()
            assert history[0]["device_count"] == 1
            assert history == p["snapshots"]
            assert client.get("/api/projects").json()[0]["device_count"] == 1
        assert "source_refs" not in p["topology"]["nodes"][0]
        assert p["snapshot"]["devices"][0]["source_refs"]
