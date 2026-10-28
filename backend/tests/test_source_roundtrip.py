"""Original source newline bytes survive import, evidence and JSON restore.

Use explicit byte payloads so this catches Windows CRLF expansion on every
platform instead of depending on Git checkout's line-ending configuration.
"""
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from backend.app.main import create_app
from backend.app.store import Source
from backend.tests.test_api import project


SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def source_payload(kind, style):
    if kind == "config":
        text = ("version 15.2\nhostname Evidence-Router\ninterface GigabitEthernet0/0\n"
                " description 原始证据\n ip address 192.0.2.1 255.255.255.252\n no shutdown\n!\nend\n")
    else:
        text = (SAMPLES / "multi-vendor-lsdb" / "cisco-ospf.txt").read_bytes().decode("utf-8")
    lines = text.splitlines()
    if style == "lf":
        return ("\n".join(lines) + "\n").encode("utf-8")
    if style == "crlf":
        return ("\r\n".join(lines) + "\r\n").encode("utf-8")
    return "".join(line + ("\r\n" if index % 2 == 0 else "\n")
                   for index, line in enumerate(lines)).encode("utf-8")


def assert_original_source(client, app, project_id, source_id, raw):
    with app.state.store.session() as session:
        source = session.scalar(select(Source).where(Source.project_id == project_id, Source.source_id == source_id))
        assert source is not None
        stored = app.state.store.root / "sources" / source.storage_key
    assert stored.read_bytes() == raw
    response = client.get(f"/api/projects/{project_id}/sources/{source_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"].encode("utf-8") == raw
    assert body["line_count"] == len(raw.decode("utf-8").splitlines())


@pytest.mark.parametrize("kind", ["config", "lsdb"])
@pytest.mark.parametrize("style", ["lf", "crlf", "mixed"])
def test_original_source_bytes_and_line_count_survive_import_export_restore(tmp_path, kind, style):
    raw = source_payload(kind, style)
    app = create_app(tmp_path, worker_mode="inline")
    with TestClient(app) as client:
        original = project(client, f"Original {kind} {style}")
        project_id = original["id"]
        if kind == "config":
            uploaded = client.post(f"/api/projects/{project_id}/imports",
                data={"expected_version": original["version"], "strategy": "merge"},
                files={"files": ("original.cfg", raw, "application/octet-stream")})
            assert uploaded.status_code == 202, uploaded.text
            assert uploaded.json()["status"] == "completed"
            facts = client.get(f"/api/projects/{project_id}").json()["snapshot"]
            source_id = facts["source_files"][0]["id"]
        else:
            uploaded = client.post(f"/api/projects/{project_id}/lsdb",
                data={"expected_version": original["version"], "name": "Original capture", "vendor": "cisco",
                      "protocol": "ospf", "scope": "0", "process_id": "1", "declared_complete": "true"},
                files={"files": ("original.txt", raw, "text/plain")})
            assert uploaded.status_code == 201, uploaded.text
            batch = uploaded.json()["batch"]
            assert batch["record_count"] == 6
            source_id = batch["source_files"][0]["id"]

        assert_original_source(client, app, project_id, source_id, raw)
        exported = client.get(f"/api/projects/{project_id}/exports/json?include_sources=true")
        assert exported.status_code == 200, exported.text[:200]
        assert exported.json()["sources"][source_id]["text"].encode("utf-8") == raw
        restored = client.post("/api/projects/restore",
            files={"file": ("original-source.iptopo.json", exported.content, "application/json")})
        assert restored.status_code == 201, restored.text[:200]
        restored_id = restored.json()["id"]
        assert restored_id != project_id
        assert_original_source(client, app, restored_id, source_id, raw)
        # Restoring into another project cannot rewrite the imported original.
        assert_original_source(client, app, project_id, source_id, raw)
