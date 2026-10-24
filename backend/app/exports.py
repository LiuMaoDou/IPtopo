from __future__ import annotations
from copy import deepcopy
import csv
import io
import json
import math
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import quote
from N2G import drawio_diagram
from .inference import project_graph

# Original identifiers shared visually with frontend/src/vendorIcons.ts, not
# downloaded vendor artwork. Package these inline so exports work offline.
_VENDOR_SYMBOLS = [
    ("Huawei", "Huawei", '<g fill="#d94b4b"><ellipse cx="32" cy="22" rx="5" ry="18"/><ellipse cx="21" cy="25" rx="5" ry="18" transform="rotate(-26 21 25)"/><ellipse cx="43" cy="25" rx="5" ry="18" transform="rotate(26 43 25)"/><ellipse cx="13" cy="34" rx="5" ry="17" transform="rotate(-52 13 34)"/><ellipse cx="51" cy="34" rx="5" ry="17" transform="rotate(52 51 34)"/><path d="M6 47h23L13 56H6zm52 0H35l16 9h7z"/></g>'),
    ("Cisco", "Cisco", '<g stroke="#2585b5" stroke-width="5" stroke-linecap="round"><path d="M7 29v10m8-18v23m9-30v35m8-42v44m8-37v35m9-28v23m8-15v10"/></g>'),
    ("Juniper", "Juniper", '<g fill="none" stroke="#3b9d72" stroke-width="4" stroke-linecap="round"><path d="M32 56V12M32 44C14 44 9 35 9 26m23 9c16 0 23-11 23-21M32 25c-9 0-15-8-15-16"/></g><g fill="#3b9d72"><circle cx="9" cy="22" r="5"/><circle cx="17" cy="7" r="5"/><circle cx="55" cy="10" r="5"/><circle cx="32" cy="8" r="5"/></g>'),
    ("Nokia", "Nokia", '<path d="M10 52V12h10l24 28V12h10v40H44L20 24v28z" fill="#4659b9"/>'),
    ("Other", "其他厂商", '<g fill="none" stroke="#718797" stroke-width="3"><ellipse cx="32" cy="18" rx="23" ry="10"/><path d="M9 18v25c0 14 46 14 46 0V18M20 14l24 8m0-8-24 8"/></g>'),
]


def _vendor_symbol(vendor):
    value = str(vendor or "").lower()
    return next((item for item in _VENDOR_SYMBOLS[:-1] if item[0].lower() in value), _VENDOR_SYMBOLS[-1])


def _symbol_style(body):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">' + body + '</svg>'
    # mxGraph separates style fields with semicolons. The valid URI form below
    # intentionally has no ;utf8 marker that would truncate the image value.
    image = "data:image/svg+xml," + quote(svg, safe="")
    return ("shape=label;image=" + image + ";imageWidth=44;imageHeight=44;imageAlign=center;imageVerticalAlign=middle;"
            "fillColor=none;strokeColor=none;whiteSpace=wrap;html=0;align=center;verticalLabelPosition=bottom;verticalAlign=top;"
            "spacingTop=8;fontColor=#172b3a;fontSize=12;")


def without_source_text(value, source_ref=False):
    """Remove configuration snippets, while retaining user-authored note text."""
    if isinstance(value, list):
        return [without_source_text(x, source_ref) for x in value]
    if isinstance(value, dict):
        return {k: without_source_text(v, source_ref or k == "source_refs")
                for k, v in value.items()
                if k not in {"raw_text", "source_text"} and not (source_ref and k == "text")}
    return value


def filter_graph(snapshot: dict, view: dict | None, mode: str):
    """Mirror the workbench filters, including isolated matching devices."""
    graph = project_graph(snapshot, mode)
    filters = (view or {}).get("filters", {})
    query = str(filters.get("search", "") or "").lower()
    vendor = filters.get("vendor", "all")
    family = str(filters.get("family", "all"))
    domain = filters.get("domain", "all")
    focus = filters.get("focus", "")
    settings = snapshot.get("domain_settings", {})
    contexts = {c["id"]: c for c in snapshot.get("contexts", [])}
    interfaces = {i["id"]: i for i in snapshot.get("interfaces", [])}
    by_device = {}
    for interface in interfaces.values():
        by_device.setdefault(interface["device_id"], []).append(interface)

    def domain_for(interface):
        ctx = contexts.get(interface.get("routing_context_id"), {})
        return (settings.get("interface_mappings", {}).get(interface["id"])
                or settings.get("mappings", {}).get(interface.get("routing_context_id"))
                or ("default" if ctx.get("kind") == "default" else f"isolated:{interface.get('routing_context_id') or interface['id']}"))

    visible = set()
    for device in snapshot.get("devices", []):
        its = by_device.get(device["id"], [])
        words = [device.get("hostname", ""), device.get("vendor", ""), device.get("os", ""), device.get("site", "")]
        for interface in its:
            words.extend([interface.get("raw_name", ""), interface.get("normalized_name", ""), interface.get("description", "")])
            for address in interface.get("addresses", []):
                words.extend([address.get("address") or "", address.get("canonical_network") or ""])
        if vendor not in {None, "", "all"} and device.get("vendor") != vendor:
            continue
        if query and query not in " ".join(str(word or "") for word in words).lower():
            continue
        if family not in {"all", "", "None"} and not any(str(a.get("family")) == family for i in its for a in i.get("addresses", [])):
            continue
        if domain not in {None, "", "all"} and not any(domain_for(i) == domain for i in its):
            continue
        visible.add(device["id"])
    for node in graph["nodes"]:
        if node.get("kind") != "segment":
            continue
        if family not in {"all", "", "None"} and str(node.get("family")) != family:
            continue
        if domain not in {None, "", "all"} and node.get("domain_id") != domain:
            continue
        if any(interfaces.get(i, {}).get("device_id") in visible for i in node.get("interface_ids", [])):
            visible.add(node["id"])
    edges = []
    for edge in graph["edges"]:
        if edge["source"] not in visible or edge["target"] not in visible:
            continue
        if family not in {"all", "", "None"} and not any(str(f) == family for f in edge.get("families", [])):
            continue
        endpoint = interfaces.get(edge.get("interface_a_id"))
        if domain not in {None, "", "all"} and not (any(e.get("domain_id") == domain for e in edge.get("evidence", [])) or (endpoint and domain_for(endpoint) == domain)):
            continue
        edges.append(edge)
    if focus:
        # Match graphModel.filterTopology: other filters first; then one logical
        # hop, expanding through a shared segment to its visible member devices.
        adjacent = defaultdict(set)
        for edge in edges:
            adjacent[edge["source"]].add(edge["target"])
            adjacent[edge["target"]].add(edge["source"])
        kept = {focus, *adjacent.get(focus, set())}
        segment_ids = {s["id"] for s in snapshot.get("segments", [])}
        for node_id in list(kept):
            if node_id in segment_ids:
                kept.update(adjacent.get(node_id, set()))
        visible.intersection_update(kept)
        edges = [e for e in edges if e["source"] in kept and e["target"] in kept]
    return {"nodes": [n for n in graph["nodes"] if n["id"] in visible], "edges": edges}


def csv_archive(snapshot: dict, graph: dict) -> bytes:
    visible = {n["id"] for n in graph["nodes"]}
    visible_segments = visible | {segment_id for edge in graph["edges"] for segment_id in edge.get("segment_ids", [])}
    interface_rows = [i for i in snapshot["interfaces"] if i["device_id"] in visible]
    data = {
        "devices": [d for d in snapshot["devices"] if d["id"] in visible],
        "interfaces": interface_rows,
        "addresses": [a for i in interface_rows for a in i.get("addresses", [])],
        "segments": [s for s in snapshot.get("segments", []) if s["id"] in visible_segments],
        "links": graph["edges"],
    }
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, rows in data.items():
            rows = without_source_text(rows)
            fields = sorted(set().union(*(r.keys() for r in rows))) if rows else ["id"]
            content = io.StringIO(newline="")
            writer = csv.DictWriter(content, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                cells = {}
                for k, v in row.items():
                    value = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else "" if v is None else str(v)
                    # Spreadsheet formula injection protection while keeping data visible.
                    cells[k] = "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
                writer.writerow(cells)
            archive.writestr(name + ".csv", "\ufeff" + content.getvalue())
    return result.getvalue()


def drawio_export(graph: dict, view: dict | None, name: str) -> bytes:
    drawing = drawio_diagram()
    drawing.add_diagram(id="iptopo", name=name)
    positions = (view or {}).get("positions", {})
    labels = (view or {}).get("labels", {})
    used_vendors = {}
    device_positions = []
    for index, node in enumerate(graph["nodes"]):
        position = positions.get(node["id"], {"x": (index % 6) * 220 + 100, "y": (index // 6) * 160 + 100})
        segment = node.get("kind") == "segment"
        symbol = _vendor_symbol(node.get("vendor"))
        style = ("shape=rhombus;whiteSpace=wrap;html=0;fillColor=#eef6ff;strokeColor=#7293bb;" if segment else _symbol_style(symbol[2]))
        node_label = (node.get("label", node["id"]) if labels.get("names", True) else "")
        if not segment:
            used_vendors[symbol[0]] = symbol
            device_positions.append(position)
        drawing.add_node(id=node["id"], label=node_label, style=style,
                         x_pos=float(position["x"]) - 70, y_pos=float(position["y"]) - 30, width=140, height=60,
                         data={"kind": node.get("kind", "device")})
    for edge in graph["edges"]:
        rejected = edge.get("review_state") == "rejected"
        if rejected:
            continue
        style = "endArrow=none;html=0;strokeColor=#7a959e;" + ("dashed=1;" if edge.get("review_state") == "pending" else "")
        edge_label = ("\n".join(edge.get("prefixes", [])) or edge.get("label", "")) if labels.get("prefixes", True) else ""
        drawing.add_link(source=edge["source"], target=edge["target"], link_id=edge["id"], label=edge_label, style=style,
                         src_label=edge.get("source_label", ""), trgt_label=edge.get("target_label", ""),
                         data={"origin": edge.get("origin", "inferred"), "review_state": edge.get("review_state", "pending"),
                               "prefixes": ", ".join(edge.get("prefixes", []))})
    xml = ET.fromstring(drawing.dump_xml())
    root = xml.find(".//root")
    grouping = (view or {}).get("groups", {}).get("mode", "none")
    if grouping in {"vendor", "site"}:
        objects = {obj.get("id"): obj for obj in root.findall("object")}
        groups = defaultdict(list)
        for node in graph["nodes"]:
            if node.get("kind") == "device":
                groups[str(node.get(grouping) or "未分类")].append(objects[node["id"]])
        for name, members in groups.items():
            geometries = [obj.find("mxCell/mxGeometry") for obj in members]
            x = min(float(g.get("x")) for g in geometries) - 30
            y = min(float(g.get("y")) for g in geometries) - 50
            right = max(float(g.get("x")) + float(g.get("width")) for g in geometries) + 30
            bottom = max(float(g.get("y")) + float(g.get("height")) for g in geometries) + 30
            group_id = "group:" + name
            group = ET.Element("mxCell", {"id": group_id, "value": name, "vertex": "1", "parent": "1", "style": "rounded=1;whiteSpace=wrap;html=0;fillColor=#f4f8fa;strokeColor=#d5e1e7;dashed=1;verticalAlign=top;spacingTop=10;container=1;collapsible=0;"})
            ET.SubElement(group, "mxGeometry", {"x": str(x), "y": str(y), "width": str(right-x), "height": str(bottom-y), "as": "geometry"})
            root.insert(2, group)
            for obj, geo in zip(members, geometries):
                obj.find("mxCell").set("parent", group_id)
                geo.set("x", str(float(geo.get("x")) - x))
                geo.set("y", str(float(geo.get("y")) - y))
    if used_vendors and root is not None:
        legend_id = "iptopo:vendor-legend"
        ordered = [s for s in _VENDOR_SYMBOLS if s[0] in used_vendors]
        x = min(float(p["x"]) for p in device_positions) - 70
        y = max(float(p["y"]) for p in device_positions) + 130
        legend = ET.SubElement(root, "mxCell", {"id": legend_id, "value": "厂商标识", "vertex": "1", "parent": "1", "style": "rounded=1;whiteSpace=wrap;html=0;fillColor=#ffffff;strokeColor=#d5e1e7;verticalAlign=top;spacingTop=10;container=1;collapsible=0;"})
        ET.SubElement(legend, "mxGeometry", {"x": str(x), "y": str(y), "width": str(max(180, len(ordered) * 90 + 20)), "height": "120", "as": "geometry"})
        for index, (key, name, body) in enumerate(ordered):
            cell = ET.SubElement(root, "mxCell", {"id": f"{legend_id}:{key}", "value": name, "vertex": "1", "parent": legend_id, "style": _symbol_style(body)})
            ET.SubElement(cell, "mxGeometry", {"x": str(10 + index * 90), "y": "30", "width": "90", "height": "50", "as": "geometry"})
    return ET.tostring(xml, encoding="utf-8")
