"""Replacement keeps current routing facts separate from import history."""
from copy import deepcopy
import io
from pathlib import Path
import re
import zipfile

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.routing.model import validate_snapshot_routing
from backend.app.store import Snapshot
from backend.tests.test_api import edit, project, upload


SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "huawei-igp"


def sample(name="01-S.cfg", *, unsupported=False):
    text = (SAMPLES / name).read_text(encoding="utf-8")
    if unsupported:
        text = text.replace(" area 0.0.0.0\n", " area 0.0.0.0\n preference 123\n", 1)
    return text.encode("utf-8")


def isis_only():
    text = sample().decode("utf-8")
    text = re.sub(r"^ospf .*?\n#\n", "", text, flags=re.MULTILINE | re.DOTALL)
    text = re.sub(r"^.*ospf .*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^interface GigabitEthernet0/0/1\n.*?\n#\n", "", text,
                  flags=re.MULTILINE | re.DOTALL)
    return text.replace("10.255.0.1", "10.255.10.1").encode("utf-8")


def source_ids(value):
    result = set()
    if isinstance(value, dict):
        if value.get("source_file_id"):
            result.add(value["source_file_id"])
        for child in value.values():
            result.update(source_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(source_ids(child))
    return result


def assert_current_routing(snapshot):
    facts = validate_snapshot_routing(snapshot)
    devices = {row["id"] for row in snapshot["devices"]}
    sources = {row["id"] for row in snapshot["source_files"]}
    assert set(facts.adapter_devices) <= devices
    for name in ("instances", "bindings", "advertisements", "issues"):
        assert {row["device_id"] for row in snapshot["routing"][name]} <= devices
        assert source_ids(snapshot["routing"][name]) <= sources


def test_same_filename_merge_removes_old_instances_bindings_prefixes_and_issues(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("a.cfg", sample(unsupported=True)), ("b.cfg", sample("02-A.cfg"))])
        before = deepcopy(previous["snapshot"])
        device = next(row["id"] for row in before["devices"] if row["hostname"] == "IGP-S")
        old_source = next(row["id"] for row in before["source_files"] if row["filename"] == "a.cfg")
        assert any(row["device_id"] == device for row in before["routing"]["issues"])

        _, current = upload(client, previous, [("a.cfg", isis_only())])
        snapshot = current["snapshot"]
        assert len(snapshot["devices"]) == 2
        assert next(row["id"] for row in snapshot["devices"] if row["hostname"] == "IGP-S") == device
        assert {row["protocol"] for row in snapshot["routing"]["instances"] if row["device_id"] == device} == {"isis"}
        assert not any(row["device_id"] == device for row in snapshot["routing"]["issues"])
        assert not any(row["address"] == "10.255.0.1" for row in snapshot["routing"]["advertisements"])
        removed_interface = next(row["id"] for row in before["interfaces"] if row["device_id"] == device and row["raw_name"] == "GigabitEthernet0/0/1")
        assert not any(row["interface_id"] == removed_interface for row in snapshot["routing"]["bindings"])
        assert old_source not in source_ids(snapshot)
        assert_current_routing(snapshot)
        historical = client.get(f"/api/projects/{current['id']}/snapshots/{previous['snapshot_id']}").json()["snapshot"]
        assert historical["routing"] == before["routing"]


def test_same_filename_changed_device_conflicts_old_override_and_removes_old_facts(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("a.cfg", sample())])
        old_device = previous["snapshot"]["devices"][0]["id"]
        previous = edit(client, previous, "override_field", {"kind": "device", "id": old_device, "field": "site"}, {"value": "Old site"})
        _, current = upload(client, previous, [("a.cfg", sample().replace(b"sysname IGP-S", b"sysname Replacement-Router"))])
        snapshot = current["snapshot"]
        assert len(snapshot["devices"]) == 1
        assert snapshot["devices"][0]["id"] != old_device
        assert snapshot["devices"][0]["site"] == ""
        assert current["overrides"][0]["status"] == "conflict"
        assert old_device not in {row["device_id"] for row in snapshot["routing"]["instances"]}
        assert_current_routing(snapshot)


def test_replace_different_filename_drops_old_routing_without_deleting_history(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("a.cfg", sample()), ("b.cfg", sample("02-A.cfg"))])
        old_devices = {row["id"] for row in previous["snapshot"]["devices"]}
        previous = edit(client, previous, "override_field", {"kind": "device", "id": next(iter(old_devices)), "field": "site"}, {"value": "Old site"})
        old_sources = {row["id"] for row in previous["snapshot"]["source_files"]}
        _, current = upload(client, previous, [("new-name.cfg", sample("03-B.cfg"))], strategy="replace")
        snapshot = current["snapshot"]
        assert [row["hostname"] for row in snapshot["devices"]] == ["IGP-B"]
        assert {row["filename"] for row in snapshot["source_files"]} == {"new-name.cfg"}
        assert not old_sources & source_ids(snapshot)
        assert not old_devices & {row["device_id"] for row in snapshot["routing"]["instances"]}
        assert current["overrides"][0]["status"] == "conflict"
        assert len(current["snapshots"]) == 2
        historical = client.get(f"/api/projects/{current['id']}/snapshots/{previous['snapshot_id']}").json()["snapshot"]
        assert {row["id"] for row in historical["devices"]} == old_devices
        assert_current_routing(snapshot)


def test_replace_parser_failure_protects_only_failed_filename_previous_facts(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("a.cfg", sample()), ("b.cfg", sample("02-A.cfg"))])
        retained = next(row["id"] for row in previous["snapshot"]["devices"] if row["hostname"] == "IGP-S")
        removed = next(row["id"] for row in previous["snapshot"]["devices"] if row["hostname"] == "IGP-A")
        job, current = upload(client, previous, [("a.cfg", b"not a supported config"), ("new.cfg", sample("03-B.cfg"))], strategy="replace")
        assert job["status"] == "completed"
        snapshot = current["snapshot"]
        assert {row["hostname"] for row in snapshot["devices"]} == {"IGP-S", "IGP-B"}
        assert retained in {row["device_id"] for row in snapshot["routing"]["instances"]}
        assert removed not in {row["device_id"] for row in snapshot["routing"]["instances"]}
        assert_current_routing(snapshot)


def test_replace_input_read_failure_explicitly_reports_preserved_prior_network(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("a.cfg", sample()), ("b.cfg", sample("02-A.cfg"))])
        job, current = upload(client, previous, [("unreadable.cfg", b"\x00broken"), ("new.cfg", sample("03-B.cfg"))], strategy="replace")
        assert job["status"] == "completed"
        assert {row["hostname"] for row in current["snapshot"]["devices"]} == {"IGP-S", "IGP-A", "IGP-B"}
        assert any(row.get("filename") == "本批次" and "保留" in row.get("message", "") for row in job["results"])
        assert_current_routing(current["snapshot"])


def archive_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, raw in entries:
            archive.writestr(name, raw)
    return buffer.getvalue()


def legacy_sources(app, snapshot_id):
    with app.state.store.session(write=True) as session:
        row = session.get(Snapshot, snapshot_id)
        data = deepcopy(row.data)
        for source in data["source_files"]:
            source.pop("identity_path", None)
            source.pop("identity_ambiguous", None)
        row.data = data


@pytest.mark.parametrize("legacy", [False, True])
def test_renamed_outer_zip_merge_replaces_unique_relative_source_and_routing(tmp_path, legacy):
    app = create_app(tmp_path, worker_mode="inline")
    with TestClient(app) as client:
        _, previous = upload(client, project(client), [("capture-1.zip", archive_bytes([("nested/router.cfg", sample(unsupported=True))]))])
        old_id = previous["snapshot"]["devices"][0]["id"]
        old_source = previous["snapshot"]["source_files"][0]["id"]
        if legacy:
            legacy_sources(app, previous["snapshot_id"])
        _, current = upload(client, previous, [("capture-2.zip", archive_bytes([("nested/router.cfg", isis_only())]))])
        snapshot = current["snapshot"]
        assert len(snapshot["devices"]) == 1
        assert snapshot["devices"][0]["id"] == old_id
        assert {row["filename"] for row in snapshot["source_files"]} == {"capture-2.zip/nested/router.cfg"}
        assert snapshot["source_files"][0]["identity_path"] == "nested/router.cfg"
        assert {row["protocol"] for row in snapshot["routing"]["instances"]} == {"isis"}
        assert not snapshot["routing"]["issues"]
        assert old_source not in source_ids(snapshot)
        assert len(current["snapshots"]) == 2
        assert_current_routing(snapshot)


@pytest.mark.parametrize("identical", [False, True])
def test_same_batch_relative_path_collision_keeps_independent_devices(tmp_path, identical):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        first = archive_bytes([("router.cfg", sample())])
        second = archive_bytes([("router.cfg", sample() if identical else sample("02-A.cfg"))])
        _, current = upload(client, project(client), [("site-a.zip", first), ("site-b.zip", second)])
        snapshot = current["snapshot"]
        assert len(snapshot["devices"]) == 2
        assert len({row["id"] for row in snapshot["devices"]}) == 2
        assert all(row["identity_ambiguous"] for row in snapshot["source_files"])
        assert sum(row["code"] == "ambiguous_import_identity" for row in snapshot["diagnostics"]) == 2
        assert len(snapshot["routing"]["instances"]) == 4
        assert_current_routing(snapshot)


@pytest.mark.parametrize("legacy", [False, True])
def test_history_relative_path_collision_does_not_guess_which_device_to_replace(tmp_path, legacy):
    app = create_app(tmp_path, worker_mode="inline")
    with TestClient(app) as client:
        _, previous = upload(client, project(client), [
            ("site-a.zip", archive_bytes([("router.cfg", sample())])),
            ("site-b.zip", archive_bytes([("router.cfg", sample("02-A.cfg"))])),
        ])
        old_ids = {row["id"] for row in previous["snapshot"]["devices"]}
        if legacy:
            legacy_sources(app, previous["snapshot_id"])
        _, current = upload(client, previous, [("unknown-site.zip", archive_bytes([("router.cfg", sample("03-B.cfg"))]))])
        snapshot = current["snapshot"]
        assert len(snapshot["devices"]) == 3
        assert old_ids < {row["id"] for row in snapshot["devices"]}
        assert any(row["code"] == "ambiguous_import_identity" for row in snapshot["diagnostics"])
        assert_current_routing(snapshot)


def test_actual_inner_path_rename_keeps_a_new_identity(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("capture-1.zip", archive_bytes([("one/router.cfg", sample())]))])
        _, current = upload(client, previous, [("capture-2.zip", archive_bytes([("two/router.cfg", sample())]))])
        assert len(current["snapshot"]["devices"]) == 2
        assert len({row["id"] for row in current["snapshot"]["devices"]}) == 2
        assert_current_routing(current["snapshot"])


def test_failed_renamed_zip_replacement_protects_matched_old_relative_source(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("capture-1.zip", archive_bytes([("router.cfg", sample()), ("other.cfg", sample("02-A.cfg"))]))])
        old_id = next(row["id"] for row in previous["snapshot"]["devices"] if row["hostname"] == "IGP-S")
        _, current = upload(client, previous, [("capture-2.zip", archive_bytes([("router.cfg", b"invalid device configuration"), ("fresh.cfg", sample("03-B.cfg"))]))], strategy="replace")
        snapshot = current["snapshot"]
        assert {row["hostname"] for row in snapshot["devices"]} == {"IGP-S", "IGP-B"}
        assert old_id in {row["id"] for row in snapshot["devices"]}
        assert_current_routing(snapshot)


def test_old_zip_alias_cannot_create_duplicate_ids_in_ambiguous_batch(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        raw = archive_bytes([("router.cfg", sample())])
        _, previous = upload(client, project(client), [("capture-1.zip", raw)])
        _, previous = upload(client, previous, [("capture-2.zip", raw)])
        assert len(previous["snapshot"]["devices"]) == 1
        _, current = upload(client, previous, [("capture-1.zip", raw), ("capture-2.zip", raw)])
        assert len(current["snapshot"]["devices"]) == 2
        assert len({row["id"] for row in current["snapshot"]["devices"]}) == 2
        assert_current_routing(current["snapshot"])


def test_zip_input_failure_preserves_unseen_files_without_duplicating_updated_device(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [("capture-1.zip", archive_bytes([("router.cfg", sample()), ("other.cfg", sample("02-A.cfg"))]))])
        old_id = next(row["id"] for row in previous["snapshot"]["devices"] if row["hostname"] == "IGP-S")
        job, current = upload(client, previous, [("capture-2.zip", archive_bytes([
            ("router.cfg", isis_only()), ("unreadable.cfg", b"\x00broken"), ("fresh.cfg", sample("03-B.cfg")),
        ]))], strategy="replace")
        snapshot = current["snapshot"]
        assert {row["hostname"] for row in snapshot["devices"]} == {"IGP-S", "IGP-A", "IGP-B"}
        assert len(snapshot["devices"]) == 3
        assert next(row["id"] for row in snapshot["devices"] if row["hostname"] == "IGP-S") == old_id
        assert {row["protocol"] for row in snapshot["routing"]["instances"] if row["device_id"] == old_id} == {"isis"}
        assert any(row.get("filename") == "本批次" and "保留" in row.get("message", "") for row in job["results"])
        assert_current_routing(snapshot)


def test_replace_resolves_old_path_ambiguity_before_next_renamed_zip_merge(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [
            ("site-a.zip", archive_bytes([("router.cfg", sample())])),
            ("site-b.zip", archive_bytes([("router.cfg", sample("02-A.cfg"))])),
        ])
        assert len(previous["snapshot"]["devices"]) == 2
        assert all(row["identity_ambiguous"] for row in previous["snapshot"]["source_files"])
        _, replaced = upload(client, previous, [("capture-c.zip", archive_bytes([("router.cfg", sample())]))], strategy="replace")
        snapshot = replaced["snapshot"]
        assert len(snapshot["devices"]) == 1
        replacement_id = snapshot["devices"][0]["id"]
        assert snapshot["source_files"][0]["identity_ambiguous"] is False

        _, current = upload(client, replaced, [("capture-d.zip", archive_bytes([("router.cfg", isis_only())]))])
        snapshot = current["snapshot"]
        assert len(snapshot["devices"]) == 1
        assert snapshot["devices"][0]["id"] == replacement_id
        assert snapshot["source_files"][0]["filename"] == "capture-d.zip/router.cfg"
        assert snapshot["source_files"][0]["identity_ambiguous"] is False
        assert {row["protocol"] for row in snapshot["routing"]["instances"]} == {"isis"}
        assert_current_routing(snapshot)


def test_partial_replace_keeps_actual_ambiguity_when_failed_old_source_is_protected(tmp_path):
    with TestClient(create_app(tmp_path, worker_mode="inline")) as client:
        _, previous = upload(client, project(client), [
            ("site-a.zip", archive_bytes([("router.cfg", sample())])),
            ("site-b.zip", archive_bytes([("router.cfg", sample("02-A.cfg"))])),
        ])
        old_b = next(row["id"] for row in previous["snapshot"]["devices"] if row["hostname"] == "IGP-A")
        _, replaced = upload(client, previous, [
            ("capture-c.zip", archive_bytes([("router.cfg", sample("03-B.cfg"))])),
            ("site-b.zip", archive_bytes([("router.cfg", b"invalid device configuration")])),
        ], strategy="replace")
        snapshot = replaced["snapshot"]
        assert {row["hostname"] for row in snapshot["devices"]} == {"IGP-A", "IGP-B"}
        assert old_b in {row["id"] for row in snapshot["devices"]}
        good_sources = [row for row in snapshot["source_files"] if row.get("device_id") and row["parse_status"] != "failed"]
        assert len(good_sources) == 2 and all(row["identity_ambiguous"] for row in good_sources)
        _, current = upload(client, replaced, [("capture-d.zip", archive_bytes([("router.cfg", sample())]))])
        assert len(current["snapshot"]["devices"]) == 3
        assert any(row["code"] == "ambiguous_import_identity" for row in current["snapshot"]["diagnostics"])
        assert_current_routing(current["snapshot"])
