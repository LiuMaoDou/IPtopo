"""Saved-view exports follow canvas visibility, including isolated devices."""
from backend.app.exports import filter_graph
from backend.app.inference import infer
from backend.tests.test_inference import topology


def mixed_snapshot():
    snapshot = topology(["10.0.0.1/24", "2001:db8::1/64"], ["10.0.0.2/24", "2001:db8::2/64"], ["10.0.0.3/24", "2001:db8::3/64"], "10.1.0.1/24", "2001:db8:2::1/64", "10.2.0.1/24")
    for index, device in enumerate(snapshot["devices"]):
        device["vendor"] = "Cisco" if index in (0, 2, 3) else "Huawei"
    snapshot["devices"][0]["hostname"] = "Paris-Core"
    snapshot["interfaces"][3]["normalized_name"] = "GigabitEthernet9/0"
    snapshot["domain_settings"] = {"domains": [{"id": "default", "name": "Default", "confirmed": False}, {"id": "customer", "name": "Customer", "confirmed": True}], "interface_mappings": {"i5": "customer"}}
    snapshot.update(infer(snapshot))
    return snapshot


def visible_devices(graph):
    return {n["id"] for n in graph["nodes"] if n["kind"] == "device"}


def segments(graph):
    return [n for n in graph["nodes"] if n["kind"] == "segment"]


def assert_no_dangling_edges(graph):
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])


def test_vendor_filter_preserves_matching_isolated_device_and_shared_segments():
    snapshot = mixed_snapshot()
    for mode in ("device", "segment"):
        graph = filter_graph(snapshot, {"filters": {"vendor": "Cisco"}}, mode)
        assert visible_devices(graph) == {"d0", "d2", "d3"}
        assert len(segments(graph)) == 2
        assert len(graph["edges"]) == 4
        assert all(e["device_a_id"] in {"d0", "d2"} for e in graph["edges"])
        assert_no_dangling_edges(graph)


def test_family_filter_keeps_isolated_matching_addresses_and_scopes_shared_segments():
    snapshot = mixed_snapshot()
    graph = filter_graph(snapshot, {"filters": {"family": "6"}}, "device")
    assert visible_devices(graph) == {"d0", "d1", "d2", "d4"}
    assert len(segments(graph)) == 1
    assert segments(graph)[0]["family"] == 6
    assert len(graph["edges"]) == 3
    assert all(e["families"] == [6] for e in graph["edges"])
    assert_no_dangling_edges(graph)


def test_domain_filter_preserves_isolated_mapped_device():
    graph = filter_graph(mixed_snapshot(), {"filters": {"domain": "customer"}}, "device")
    assert visible_devices(graph) == {"d5"}
    assert not segments(graph)
    assert not graph["edges"]


def test_domain_default_excludes_other_domain_but_keeps_isolated_members():
    graph = filter_graph(mixed_snapshot(), {"filters": {"domain": "default"}}, "device")
    assert visible_devices(graph) == {"d0", "d1", "d2", "d3", "d4"}
    assert len(segments(graph)) == 2
    assert len(graph["edges"]) == 6


def test_prefix_search_keeps_matching_devices_and_related_shared_segments():
    graph = filter_graph(mixed_snapshot(), {"filters": {"search": "10.0.0.0/24"}}, "device")
    assert visible_devices(graph) == {"d0", "d1", "d2"}
    # Search chooses devices; each eligible member keeps its related segments.
    assert len(segments(graph)) == 2
    assert len(graph["edges"]) == 6
    assert_no_dangling_edges(graph)


def test_search_hostname_case_insensitive_and_normalized_interface_name():
    snapshot = mixed_snapshot()
    graph = filter_graph(snapshot, {"filters": {"search": "pARIS"}}, "device")
    assert visible_devices(graph) == {"d0"}
    assert len(segments(graph)) == 2
    assert len(graph["edges"]) == 2
    graph = filter_graph(snapshot, {"filters": {"search": "GigabitEthernet9/0"}}, "device")
    assert visible_devices(graph) == {"d3"}
    assert not graph["edges"]


def test_combined_filters_leave_only_matching_members_without_clique():
    graph = filter_graph(mixed_snapshot(), {"filters": {"vendor": "Cisco", "family": "6", "domain": "default", "search": "2001:db8::/64"}}, "segment")
    assert visible_devices(graph) == {"d0", "d2"}
    assert len(segments(graph)) == 1
    assert len(graph["edges"]) == 2
    assert {e["target"] for e in graph["edges"]} == {segments(graph)[0]["id"]}
    assert_no_dangling_edges(graph)


def test_family_filter_on_dual_stack_pair_does_not_keep_other_family_segment():
    snapshot = topology(["10.0.0.0/31", "2001:db8::/127"], ["10.0.0.1/31", "2001:db8::1/127"])
    snapshot.update(infer(snapshot))
    graph = filter_graph(snapshot, {"filters": {"family": "6"}}, "segment")
    assert visible_devices(graph) == {"d0", "d1"}
    assert len(segments(graph)) == 1
    assert segments(graph)[0]["family"] == 6
    assert len(graph["edges"]) == 2
    assert all(e["families"] == [6] for e in graph["edges"])


def test_drawio_keeps_group_geometry_and_label_preferences():
    from backend.app.exports import drawio_export
    import xml.etree.ElementTree as ET
    graph = {"nodes": [{"id": "a", "label": "Router A", "kind": "device", "vendor": "Cisco"},
                       {"id": "b", "label": "Router B", "kind": "device", "vendor": "Cisco"}],
             "edges": [{"id": "link", "source": "a", "target": "b", "label": "Gi0 ↔ Gi1", "prefixes": ["10.0.0.0/31"], "source_label": "Gi0", "target_label": "Gi1"}]}
    view = {"groups": {"mode": "vendor"}, "labels": {"names": True, "vendors": False, "prefixes": False},
            "positions": {"a": {"x": 100, "y": 200}, "b": {"x": 400, "y": 200}}}
    xml = ET.fromstring(drawio_export(graph, view, "grouped"))
    group = xml.find(".//mxCell[@id='group:Cisco']")
    assert group is not None
    node = xml.find(".//object[@id='a']")
    assert node.get("label") == "Router A"
    assert node.find("mxCell").get("parent") == "group:Cisco"
    geo = node.find("mxCell/mxGeometry")
    group_geo = group.find("mxGeometry")
    assert float(geo.get("x")) + float(group_geo.get("x")) == 100 - 70
    assert float(geo.get("y")) + float(group_geo.get("y")) == 200 - 30
    link = xml.find(".//object[@id='link_id:link']")
    assert link.get("label") == ""
    labels = {c.get("value") for c in xml.findall(".//mxCell") if c.get("parent") == "link_id:link"}
    assert {"Gi0", "Gi1"} <= labels


def test_focus_device_expands_shared_segment_without_unrelated_devices():
    snapshot = mixed_snapshot()
    for mode in ("device", "segment"):
        graph = filter_graph(snapshot, {"filters": {"focus": "d0"}}, mode)
        assert visible_devices(graph) == {"d0", "d1", "d2"}
        assert len(segments(graph)) == 2
        assert len(graph["edges"]) == 6
        assert_no_dangling_edges(graph)


def test_focus_segment_includes_member_devices_then_applies_other_filters():
    snapshot = mixed_snapshot()
    selected = next(s for s in snapshot["segments"] if s["prefix"] == "10.0.0.0/24")
    graph = filter_graph(snapshot, {"filters": {"focus": selected["id"], "vendor": "Cisco", "family": "4"}}, "segment")
    assert visible_devices(graph) == {"d0", "d2"}
    assert len(segments(graph)) == 1
    assert len(graph["edges"]) == 2
    assert_no_dangling_edges(graph)


def test_focus_pair_is_one_hop_and_does_not_expand_neighbors_neighbors():
    snapshot = topology(["10.0.0.0/31", "10.0.2.0/31"], ["10.0.0.1/31", "10.0.1.0/31"], "10.0.1.1/31", "10.0.2.1/31")
    snapshot.update(infer(snapshot))
    for mode in ("device", "segment"):
        graph = filter_graph(snapshot, {"filters": {"focus": "d0"}}, mode)
        assert visible_devices(graph) == {"d0", "d1", "d3"}
        if mode == "segment":
            assert len(segments(graph)) == 2
        assert_no_dangling_edges(graph)


def test_focus_excluded_by_other_filters_produces_empty_graph():
    snapshot = mixed_snapshot()
    graph = filter_graph(snapshot, {"filters": {"focus": "d0", "vendor": "Huawei"}}, "device")
    assert graph == {"nodes": [], "edges": []}


def test_focus_on_isolated_device_preserves_it_and_empty_csv_schema():
    import csv
    import io
    import zipfile
    from backend.app.exports import csv_archive
    snapshot = mixed_snapshot()
    graph = filter_graph(snapshot, {"filters": {"focus": "d3"}}, "device")
    assert visible_devices(graph) == {"d3"}
    assert not graph["edges"]
    with zipfile.ZipFile(io.BytesIO(csv_archive(snapshot, graph))) as archive:
        assert list(csv.DictReader(io.StringIO(archive.read("segments.csv").decode("utf-8-sig")))) == []
        assert len(list(csv.DictReader(io.StringIO(archive.read("devices.csv").decode("utf-8-sig"))))) == 1


def test_csv_segments_include_folded_pair_segment_once():
    import csv
    import io
    import zipfile
    from backend.app.exports import csv_archive
    snapshot = topology("10.0.0.0/31", "10.0.0.1/31")
    snapshot.update(infer(snapshot))
    graph = filter_graph(snapshot, None, "device")
    assert not segments(graph)
    with zipfile.ZipFile(io.BytesIO(csv_archive(snapshot, graph))) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("segments.csv").decode("utf-8-sig"))))
        assert [r["id"] for r in rows] == [snapshot["segments"][0]["id"]]


def test_drawio_uses_distinct_vendor_symbols_names_below_and_separate_legend():
    from urllib.parse import unquote
    import xml.etree.ElementTree as ET
    from backend.app.exports import drawio_export
    graph = {"nodes": [{"id": str(n), "label": f"Router-{n}", "kind": "device", "vendor": vendor}
                        for n, vendor in enumerate(("Huawei", "Cisco", "Juniper", "Nokia", "Unknown"))], "edges": []}
    xml = ET.fromstring(drawio_export(graph, None, "Vendor symbols"))
    images = []
    for n in range(5):
        obj = xml.find(f".//object[@id='{n}']")
        assert obj.get("label") == f"Router-{n}"
        style = obj.find("mxCell").get("style")
        assert "verticalLabelPosition=bottom" in style
        image = next(part.removeprefix("image=") for part in style.split(";") if part.startswith("image="))
        assert image.startswith("data:image/svg+xml,")
        ET.fromstring(unquote(image.split(",", 1)[1]))
        images.append(image)
    assert len(set(images)) == 5
    legend = xml.find(".//mxCell[@id='iptopo:vendor-legend']")
    assert legend.get("value") == "厂商标识"
    labels = {node.get("value") for node in xml.findall(".//mxCell") if node.get("parent") == "iptopo:vendor-legend"}
    assert labels == {"Huawei", "Cisco", "Juniper", "Nokia", "其他厂商"}
