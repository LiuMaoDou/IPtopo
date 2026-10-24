"""Connected multi-interface 1,000-device acceptance through public HTTP routes."""
from fastapi.testclient import TestClient

from backend.app.main import create_app
from scripts.generate_scale_samples import create_archive


def test_thousand_device_connected_topology_counts_and_domain_isolation(tmp_path):
    count = 1000
    archive = tmp_path / "ring.zip"
    expected = create_archive(count, archive)
    with TestClient(create_app(tmp_path / "data", worker_mode="inline")) as client:
        project = client.post("/api/projects", json={"name": "1,000 device acceptance"}).json()
        response = client.post(f"/api/projects/{project['id']}/imports", files={"files": (archive.name, archive.read_bytes(), "application/zip")}, data={"expected_version": project["version"]})
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "completed"
        project = client.get(f"/api/projects/{project['id']}").json()
        snapshot = project["snapshot"]
        assert len(snapshot["devices"]) == count
        assert len(snapshot["interfaces"]) == expected["interfaces"]
        assert len(snapshot["contexts"]) == expected["routing_contexts"]
        assert len(snapshot["links"]) == expected["logical_links"]
        assert len(snapshot["segments"]) == expected["segments"]
        assert sum(len(i["addresses"]) for i in snapshot["interfaces"]) == expected["ipv4_addresses"] + expected["ipv6_addresses"]
        assert sum(l["families"] == [4, 6] for l in snapshot["links"]) == expected["dual_stack_links"]
        assert all(l["review_state"] == "pending" for l in snapshot["links"])
        assert not any(d["code"] in {"duplicate_ip", "prefix_overlap", "invalid_address"} for d in snapshot["diagnostics"])
        assert len(project["topology"]["nodes"]) == count
        assert len(project["topology"]["edges"]) == count * 3 // 2
        adjacent = {d["id"]: set() for d in snapshot["devices"]}
        for link in snapshot["links"]:
            adjacent[link["device_a_id"]].add(link["device_b_id"])
            adjacent[link["device_b_id"]].add(link["device_a_id"])
        assert {len(peers) for peers in adjacent.values()} == {3}
        visited, frontier = set(), [snapshot["devices"][0]["id"]]
        while frontier:
            device = frontier.pop()
            if device not in visited:
                visited.add(device)
                frontier.extend(adjacent[device] - visited)
        assert len(visited) == count

        # Splitting the two halves into address domains removes all cross-domain
        # chords and the two ring boundary links; all other endpoints survive.
        first_half = {device["id"] for device in snapshot["devices"][:count // 2]}
        domains = {"domains": [{"id": "site-a", "name": "Site A", "confirmed": True}, {"id": "site-b", "name": "Site B", "confirmed": True}],
                   "mappings": {context["id"]: "site-a" if context["device_id"] in first_half else "site-b" for context in snapshot["contexts"]},
                   "interface_mappings": {}, "segment_splits": {}}
        response = client.put(f"/api/projects/{project['id']}/domains", json={"version": project["version"], "domains": domains})
        assert response.status_code == 200, response.text
        isolated = response.json()["snapshot"]
        assert len(isolated["devices"]) == count
        assert len(isolated["links"]) == count - 2
        assert all((l["device_a_id"] in first_half) == (l["device_b_id"] in first_half) for l in isolated["links"])
        assert {e["domain_id"] for l in isolated["links"] for e in l["evidence"]} == {"site-a", "site-b"}
