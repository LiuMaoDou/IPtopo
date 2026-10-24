"""Configuration-only interface parser with explicit dialect and source provenance.

Context adapters scope input before TTP field extraction. Never execute configuration,
merge devices by hostname, or turn configured addresses into operational-state claims.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import shlex
from functools import lru_cache
from importlib.resources import files as package_files
from importlib.metadata import PackageNotFoundError, version
from pathlib import PurePath

from ttp import ttp

PARSER_VERSION = "1.1.0"
_JUNOS_INTERFACE = r"(?:(?:ge|xe|et|fe)-\d+/\d+/\d+(?::\d+)?|(?:ae|reth|lo|demux)\d+|irb|vlan)"


def _junos_capture(text):
    """Remove CLI framing without shifting evidence lines; retain explicit scope."""
    scope = []
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        command = re.sub(r"^\S+@\S+[>#]\s*", "", stripped).split("|", 1)[0].strip()
        if re.match(r"^show\s+configuration(?:\s|$)", command):
            path = _tokens(command)[2:]
            if path[:1] == ["interfaces"]:
                scope = path
            lines.append("")
        elif re.fullmatch(r"\[edit(?:\s+[^\]]+)?\]", stripped):
            path = _tokens(stripped[1:-1])[1:]
            if path[:1] == ["interfaces"]:
                scope = path
            lines.append("")
        elif re.fullmatch(r"\S+@\S+[>#]", stripped) or re.fullmatch(r"\{(?:master|backup|primary|secondary)(?::\d+)?\}", stripped):
            lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines), scope
SUPPORTED_DIALECTS = [
    {"id": "huawei_vrp", "label": "Huawei VRP", "vendor": "Huawei", "os": "VRP", "format": "current-configuration", "notes": "Interface IPv4/IPv6, VPN instance, Eth-Trunk, VLAN and subinterfaces"},
    {"id": "cisco_ios", "label": "Cisco IOS / IOS-XE", "vendor": "Cisco", "os": "IOS/IOS-XE", "format": "running-config", "notes": "Interface IPv4/IPv6, VRF, Port-channel, SVI, HSRP/VRRP"},
    {"id": "cisco_xr", "label": "Cisco IOS-XR", "vendor": "Cisco", "os": "IOS-XR", "format": "running-config", "notes": "Interface IPv4/IPv6, VRF, Bundle-Ether and subinterfaces"},
    {"id": "cisco_nxos", "label": "Cisco NX-OS", "vendor": "Cisco", "os": "NX-OS", "format": "running-config", "notes": "Interface IPv4/IPv6, VRF member, Port-channel, SVI and anycast marker"},
    {"id": "junos_set", "label": "Juniper Junos · display set", "vendor": "Juniper", "os": "Junos", "format": "set", "notes": "inet/inet6, units, routing-instances, ae; apply-groups inheritance is diagnosed"},
    {"id": "junos_hierarchical", "label": "Juniper Junos · hierarchical", "vendor": "Juniper", "os": "Junos", "format": "hierarchical", "notes": "Brace syntax, inactive blocks, inet/inet6; apply-groups inheritance is diagnosed"},
    {"id": "nokia_classic", "label": "Nokia SR OS · Classic CLI", "vendor": "Nokia", "os": "SR OS", "format": "classic", "notes": "Base/VPRN named interfaces, port/SAP, IPv4/IPv6; see support matrix"},
    {"id": "nokia_md_flat", "label": "Nokia SR OS · MD-CLI flat", "vendor": "Nokia", "os": "SR OS", "format": "md-flat", "notes": "Absolute /configure paths; router/VPRN IPv4 primary/secondary and IPv6 address"},
    {"id": "nokia_md_hierarchical", "label": "Nokia SR OS · MD-CLI hierarchical", "vendor": "Nokia", "os": "SR OS", "format": "md-hierarchical", "notes": "Brace configuration; router/VPRN address and prefix-length leaf pairs"},
]
_DIALECTS = {d["id"]: d for d in SUPPORTED_DIALECTS}
_ALIASES = {"ios": "cisco_ios", "iosxe": "cisco_ios", "ios-xe": "cisco_ios", "iosxr": "cisco_xr", "nxos": "cisco_nxos", "vrp": "huawei_vrp", "junos": "junos_set", "sros": "nokia_classic"}

# Capture fields with TTP, retaining original line number. Input is scoped to one
# interface by the dialect adapters; routes, neighbors and ACLs never reach it.
_FIELDS = """<group name="fields*">
L{{ line | _start_ }} description {{ description | ORPHRASE }}
L{{ line | _start_ }} {{ family | re("ipv?[46]?") }} address {{ address_value | ORPHRASE }}
L{{ line | _start_ }} ip unnumbered {{ unnumbered | ORPHRASE }}
L{{ line | _start_ }} ipv4 unnumbered {{ unnumbered | ORPHRASE }}
L{{ line | _start_ }} ip binding vpn-instance {{ vrf }}
L{{ line | _start_ }} ip vrf forwarding {{ vrf }}
L{{ line | _start_ }} vrf forwarding {{ vrf }}
L{{ line | _start_ }} vrf member {{ vrf }}
L{{ line | _start_ }} vrf {{ vrf }}
L{{ line | _start_ }} channel-group {{ aggregate }} mode {{ mode }}
L{{ line | _start_ }} channel-group {{ aggregate }}
L{{ line | _start_ }} eth-trunk {{ aggregate }}
L{{ line | _start_ }} bundle id {{ aggregate }} mode {{ mode }}
L{{ line | _start_ }} encapsulation dot1Q {{ vlan }}
L{{ line | _start_ }} encapsulation dot1q {{ vlan }}
L{{ line | _start_ }} vlan-type dot1q {{ vlan }}
L{{ line | _start_ }} dot1q termination vid {{ vlan }}
</group>"""


def _id(*parts):
    return hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()[:24]


def _version(package):
    try:
        return version(package)
    except PackageNotFoundError:
        return "unavailable"


def detect_dialect(text):
    if re.search(r"(?m)^\s*(?:set )?/configure[ \t]+[A-Za-z]", text):
        return "nokia_md_flat"
    if re.search(r"(?m)^\s*(?:configure\s*\{|/configure\s*\{)", text) or ("prefix-length" in text and re.search(r'router\s+"?Base"?\s*\{', text)):
        return "nokia_md_hierarchical"
    if re.search(r"(?m)^\s*(?:set|deactivate)\s+(?:system|interfaces|routing-instances)\s", text):
        return "junos_set"
    if re.search(r"\b(?:interfaces|system)\s*\{", text) and ("family inet" in text or "host-name" in text or "unit " in text):
        return "junos_hierarchical"
    if re.search(rf"(?m)^\s*(?:inactive:\s*)?{_JUNOS_INTERFACE}\s*\{{", text) and re.search(r"\bfamily\s+inet6?\b", text):
        return "junos_hierarchical"
    captured, scope = _junos_capture(text)
    if scope and re.search(r"\bfamily\s+inet6?\b|\baddress\s+[0-9a-fA-F:.]+/\d+", captured):
        return "junos_set" if re.search(r"(?m)^\s*set\s+", captured) else "junos_hierarchical"
    if re.search(r"(?mi)^\s*sysname\s|^\s*ip binding vpn-instance\s|^\s*interface\s+(?:Eth-Trunk|Vlanif)", text):
        return "huawei_vrp"
    if re.search(r"(?mi)IOS[ -]XR|^\s*interface\s+Bundle-Ether|^\s*ipv4 address\s", text):
        return "cisco_xr"
    if re.search(r"(?mi)NX-OS|^\s*feature\s|^\s*boot nxos\s|^\s*vrf member\s|^\s*fabric forwarding", text):
        return "cisco_nxos"
    if re.search(r"(?mi)TiMOS|^\s*router\s+(?:Base|[\"0-9])|^\s*service\s*$", text) or ("configure" in text and re.search(r"(?m)^\s*system\s*$", text)):
        return "nokia_classic"
    if re.search(r"(?mi)^hostname\s|^interface\s|^version\s+\d", text):
        return "cisco_ios"
    return None


class _Builder:
    def __init__(self, text, filename, source_file_id, device_id, dialect):
        self.text = text
        self.lines = text.splitlines()
        self.filename = filename
        self.source = source_file_id
        self.device_id = device_id
        self.dialect = dialect
        self.meta = _DIALECTS.get(dialect, {"vendor": "Unknown", "os": "Unknown", "format": "unknown"})
        self.hostname = PurePath(filename).stem
        self.hostname_line = 1
        self.interfaces = {}
        self.contexts = {}
        self.diagnostics = []
        self.inactive_paths = []
        self.path_sources = {}
        self.context("default")

    def ref(self, line):
        return {"source_file_id": self.source, "line": int(line), "text": self.lines[int(line)-1] if 0 < int(line) <= len(self.lines) else "", "parser": self.dialect or "unknown"}

    def warn(self, code, message, line=None, iface=None, severity="warning"):
        item = {"id": _id(self.source, code, line, len(self.diagnostics)), "severity": severity, "code": code, "message": message, "source_file_id": self.source, "device_id": self.device_id}
        if line:
            item["line"] = int(line)
        if iface:
            item["interface_id"] = iface["id"]
        self.diagnostics.append(item)

    def context(self, name, line=None):
        if name in ("Base", "default", "global", ""):
            name = "default"
        if name not in self.contexts:
            self.contexts[name] = {"id": _id(self.device_id, "context", name), "device_id": self.device_id, "local_name": name, "kind": "default" if name == "default" else "vrf", "rd": None, "rt": [], "source_refs": [self.ref(line)] if line else []}
        return self.contexts[name]

    def iface(self, name, line, context="default", inactive=False):
        # SR OS permits the same interface name in different routers/services.
        key = (context, name) if self.dialect.startswith("nokia") else ("", name)
        if key not in self.interfaces:
            low = name.lower()
            kind = "physical"
            if re.match(r"^(?:loopback|lo\d)", low) or low == "system":
                kind = "loopback"
            elif "." in name:
                kind = "subinterface"
            elif low.startswith(("eth-trunk", "port-channel", "bundle-ether", "ae", "lag-")):
                kind = "aggregate"
            elif low.startswith(("vlan", "irb", "bvi")):
                kind = "svi"
            elif low.startswith(("tunnel", "tun")):
                kind = "tunnel"
            elif self.dialect.startswith("nokia"):
                kind = "logical"
            normalized = name
            if self.dialect.startswith("cisco"):
                try:
                    from netutils.interface import canonical_interface_name
                    normalized = canonical_interface_name(name)
                except (ImportError, ValueError, KeyError):
                    pass
            self.interfaces[key] = {"id": _id(self.device_id, "interface", *key), "device_id": self.device_id, "routing_context_id": self.context(context, line)["id"], "raw_name": name, "normalized_name": normalized, "kind": kind, "parent_interface_id": None, "physical_port_refs": [], "aggregate_members": [], "encapsulation": None, "description": "", "admin_state": "unknown", "oper_state": "unknown", "effective_state": "inactive" if inactive else "active", "source_refs": [self.ref(line)], "addresses": []}
        iface = self.interfaces[key]
        if inactive:
            iface["effective_state"] = "inactive"
        return iface

    def address(self, iface, value, line, family=None, role="primary", acquisition="static", inactive=False):
        if value is None:
            addr = None
            prefix = network = None
        else:
            try:
                parsed = ipaddress.ip_interface(value)
                addr, prefix, network = str(parsed.ip), parsed.network.prefixlen, str(parsed.network)
                if family and parsed.version != family:
                    raise ValueError("address family does not match interface family")
                family = parsed.version
                if parsed.ip.is_link_local:
                    role = "link_local"
                if parsed.ip.is_unspecified or parsed.ip.is_multicast:
                    raise ValueError("unspecified or multicast interface address")
            except ValueError as exc:
                self.warn("invalid_address", f"Invalid interface address {value!r}: {exc}", line, iface)
                return
        state = "inactive" if inactive or iface["effective_state"] == "inactive" else ("unresolved" if acquisition in ("dhcp", "slaac", "unnumbered", "unresolved") else iface["effective_state"])
        item = {"id": _id(iface["id"], addr, prefix, role, acquisition), "interface_id": iface["id"], "family": family or 4, "address": addr, "prefix_length": prefix, "canonical_network": network, "role": role, "acquisition_method": acquisition, "effective_state": state, "source_refs": [self.ref(line)]}
        old = next((a for a in iface["addresses"] if a["id"] == item["id"]), None)
        if old:
            if item["source_refs"][0] not in old["source_refs"]:
                old["source_refs"].extend(item["source_refs"])
        else:
            iface["addresses"].append(item)

    def field_address(self, iface, value, line, family):
        words = value.split()
        if not words:
            return
        if words[0] in ("dhcp", "dhcp-alloc", "negotiated", "autoconfig", "auto"):
            acquisition = "slaac" if words[0] in ("autoconfig", "auto") else "unresolved" if words[0] == "negotiated" else "dhcp"
            self.address(iface, None, line, family, "unknown", acquisition)
            return
        role = "secondary" if any(w in words for w in ("secondary", "sub")) else "primary"
        address = words[0]
        if "eui-64" in words:
            self.address(iface, None, line, family, "unknown", "derived")
            iface["addresses"][-1]["effective_state"] = "unresolved"
            self.warn("derived_address", "EUI-64 host address cannot be resolved from this configuration alone.", line, iface)
            return
        if "/" not in address:
            if len(words) > 1 and (re.fullmatch(r"\d{1,3}", words[1]) or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", words[1])):
                mask = words[1]
                # ipaddress accepts host masks; interface configuration requires netmasks.
                if "." in mask:
                    try:
                        bits = format(int(ipaddress.IPv4Address(mask)), "032b")
                        if "01" in bits:
                            raise ValueError("non-contiguous netmask")
                    except ValueError:
                        self.warn("invalid_netmask", f"Invalid interface netmask {mask!r}.", line, iface)
                        return
                address += "/" + mask
            elif family == 6 and "link-local" in words:
                address += "/64"
                self.warn("link_local_prefix_assumed", "Explicit link-local address has no prefix; /64 is used only for display and excluded from inference.", line, iface, "info")
            else:
                self.warn("missing_prefix", f"Interface address {address!r} has no supported prefix or mask.", line, iface)
                return
        self.address(iface, address, line, family, role)

    def finish(self):
        for key, iface in list(self.interfaces.items()):
            if "." in iface["raw_name"]:
                parent_name = iface["raw_name"].rsplit(".", 1)[0]
                parent_key = (key[0], parent_name)
                if parent_key in self.interfaces:
                    iface["parent_interface_id"] = self.interfaces[parent_key]["id"]
                    parent = self.interfaces[parent_key]
                    if parent["effective_state"] in ("inactive", "unresolved"):
                        iface["effective_state"] = parent["effective_state"]
                    if parent["admin_state"] == "disabled":
                        iface["admin_state"] = "disabled"
            if iface["effective_state"] in ("inactive", "unresolved"):
                for address in iface["addresses"]:
                    address["effective_state"] = iface["effective_state"]
        status = "partial" if any(d["severity"] in ("warning", "error") for d in self.diagnostics) else "success"
        if not self.interfaces:
            self.warn("no_interfaces", "No supported interface declarations were parsed. This is not proof that the device has no Layer-3 addresses.")
            status = "failed" if not self.dialect else "partial"
        elif not any(i["addresses"] for i in self.interfaces.values()):
            self.warn("no_addresses", "Interfaces were parsed, but no supported Layer-3 addresses were found; verify the source scope.", severity="info")
        device = {"id": self.device_id, "hostname": self.hostname, **{k: self.meta[k] for k in ("vendor", "os", "format")}, "site": "", "source_file_id": self.source, "identity_key": self.device_id, "parse_status": status, "source_refs": [self.ref(self.hostname_line)]}
        return {"device": device, "interfaces": list(self.interfaces.values()), "contexts": list(self.contexts.values()), "diagnostics": self.diagnostics, "status": status, **{k: self.meta[k] for k in ("vendor", "os", "format")}, "parser_versions": {"iptopo": PARSER_VERSION, "ttp": _version("ttp"), "ttp_templates": _version("ttp-templates"), "netutils": _version("netutils")}}


def _ttp_fields(lines):
    parser = ttp(data="\n".join(f"L{n} {line.strip()}" for n, line in lines), template=_FIELDS)
    parser.parse(one=True)
    result = parser.result(structure="flat_list")
    return [field for chunk in result if isinstance(chunk, dict) for field in chunk.get("fields", [])]


@lru_cache(maxsize=4)
def _platform_template(name):
    """Load one of our fixed bundled assets, including in symlinked virtualenvs.

    ttp-templates 0.6.18 compares a realpath file with an abspath package root;
    its containment test rejects legitimate assets when the venv is a symlink.
    Resource loading avoids that upstream mismatch without accepting arbitrary
    filesystem input. The normal public API remains the preferred path.
    """
    allowed = {"cisco_ios_show_running_config_pipe_section_interface", "cisco_xr_show_running_config_interface", "cisco_nxos_show_running_config_interface", "juniper_junos_show_configuration_interfaces_pipe_display_set"}
    if name not in allowed:
        raise ValueError("Only registered platform templates may be loaded")
    import ttp_templates
    try:
        return ttp_templates.get_template(path=f"platform/{name}.txt")
    except ValueError as exc:
        if "resolves outside the package directory" not in str(exc):
            raise
        return package_files("ttp_templates").joinpath("platform", f"{name}.txt").read_text(encoding="utf-8")


def _upstream_check(b, interface_text):
    """Reuse upstream platform extraction as an independent coverage cross-check.

    Upstream output has no original line IDs and different default-state semantics.
    Only source-confirmed adapter records enter the facts model; a mismatch surfaces.
    """
    names = {"cisco_ios": "cisco_ios_show_running_config_pipe_section_interface", "cisco_xr": "cisco_xr_show_running_config_interface", "cisco_nxos": "cisco_nxos_show_running_config_interface", "junos_set": "juniper_junos_show_configuration_interfaces_pipe_display_set"}
    if b.dialect not in names or not interface_text:
        return
    try:
        template = _platform_template(names[b.dialect])
        engine = ttp(data=interface_text, template=template)
        engine.parse(one=True)
        results = engine.result(structure="flat_list")
        found = set()
        def visit(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k in ("ip", "ip_address") and isinstance(v, str):
                        try:
                            found.add(str(ipaddress.ip_interface(v)))
                        except ValueError:
                            pass
                    visit(v)
            elif isinstance(value, list):
                for v in value:
                    visit(v)
        visit(results)
        ours = {f"{a['address']}/{a['prefix_length']}" for i in b.interfaces.values() for a in i["addresses"] if a["address"]}
        extra = found - ours
        if extra:
            b.warn("template_coverage_gap", "Upstream template found address candidates not resolved by the source-aware adapter; review the source.", severity="warning")
    except Exception as exc:
        b.warn("upstream_template_unavailable", f"Upstream cross-check unavailable ({type(exc).__name__}); source-aware TTP extraction still ran.", severity="info")


def _parse_cli(b):
    blocks = []
    active = None
    vrf_context = None
    for n, raw in enumerate(b.lines, 1):
        line = raw.strip()
        host = re.match(r"(?:hostname|sysname)\s+(\S+)", line)
        if host:
            b.hostname, b.hostname_line = host.group(1).strip('"'), n
        context = re.match(r"(?:ip vpn-instance|vrf definition|ip vrf|vrf context)\s+(\S+)$", line)
        if context:
            vrf_context = b.context(context.group(1), n)
        elif vrf_context and re.match(r"(?:route-distinguisher|rd)\s+", line):
            vrf_context["rd"] = line.split()[-1]
        elif vrf_context and re.match(r"(?:vpn-target|route-target)\s+", line):
            vrf_context["rt"].append(line.split(maxsplit=1)[1])
        m = re.match(r"interface\s+([^\s]+)\s*$", line)
        if m:
            active = (b.iface(m.group(1), n), [])
            blocks.append(active)
            vrf_context = None
        elif re.match(r"interface\s+(?:range|preconfigure)\b", line):
            active = None
            b.warn("unsupported_interface_declaration", "Interface ranges/preconfigure require expansion and were not applied.", n)
        elif line in ("!", "#", "end", "return") or (raw and not raw[0].isspace() and not line.startswith(("!", "#"))):
            active = None
        elif active and line:
            active[1].append((n, line))
    aggregates = []
    for iface, lines in blocks:
        parsed_fields = _ttp_fields(lines)
        for field in parsed_fields:
            n = int(field["line"])
            if "description" in field:
                iface["description"] = field["description"].strip('"')
            if "vrf" in field:
                iface["routing_context_id"] = b.context(field["vrf"], n)["id"]
            if "address_value" in field:
                b.field_address(iface, field["address_value"], n, 6 if field["family"] == "ipv6" else 4)
            if "unnumbered" in field:
                b.address(iface, None, n, 4, "unknown", "unnumbered")
                iface["borrowed_interface"] = field["unnumbered"]
            if "vlan" in field:
                iface["encapsulation"] = {"type": "dot1q", "vlan_id": field["vlan"]}
            if "aggregate" in field:
                prefix = "Eth-Trunk" if b.dialect == "huawei_vrp" else "Bundle-Ether" if b.dialect == "cisco_xr" else "port-channel" if b.dialect == "cisco_nxos" else "Port-channel"
                aggregates.append((iface, prefix + field["aggregate"], n))
        for n, line in lines:
            if line == "shutdown":
                iface["admin_state"] = "disabled"
            elif line in ("no shutdown", "undo shutdown"):
                iface["admin_state"] = "enabled"
            elif line == "fabric forwarding mode anycast-gateway":
                for a in iface["addresses"]:
                    if a["role"] != "link_local":
                        a["role"] = "anycast"
                        a["id"] = _id(iface["id"], a["address"], a["prefix_length"], a["role"], a["acquisition_method"])
            virtual = re.match(r"(?:standby\s+\d+\s+ip|vrrp\s+\d+\s+ip|vrrp vrid\s+\d+\s+virtual-ip)\s+(\S+)", line)
            if virtual:
                ip = virtual.group(1)
                primary = next((a for a in iface["addresses"] if a["family"] == 4 and a["prefix_length"] is not None), None)
                b.address(iface, ip + "/" + str(primary["prefix_length"] if primary else 32), n, 4, "virtual")
            if re.match(r"(?:ip|ipv4|ipv6) (?:address|unnumbered)\b", line) and not any(int(f["line"]) == n for f in parsed_fields):
                b.warn("unsupported_address_syntax", "Interface address syntax was not recognized.", n, iface)
            if re.match(r"(?:ip|ipv4|ipv6) address\s+\S+\s+(?:dhcp|general-prefix)\b", line):
                b.warn("unsupported_address_syntax", "Address expression requires additional resolution.", n, iface)
    for member, name, n in aggregates:
        if ("", name) not in b.interfaces:
            b.warn("missing_aggregate_declaration", f"Aggregate {name} is referenced by a member but has no parsed interface declaration.", n, member)
        lag = b.iface(name, n)
        lag["aggregate_members"].append(member["id"])
        member["aggregate_interface_id"] = lag["id"]
    _upstream_check(b, "\n".join("interface " + iface["raw_name"] + "\n" + "\n".join(" " + line for _, line in lines) + "\n!" for iface, lines in blocks))


def _tokens(text):
    try:
        return shlex.split(text, comments=False)
    except ValueError:
        return text.replace('"', "").split()


def _hierarchy(b, newline_leaves=False):
    """Flatten brace syntax while retaining original leaf lines and inactive ancestry."""
    token_re = re.compile(r'"(?:\\.|[^"\\])*"|#[^\n]*|/\*.*?\*/|[{};\n]|[^\s{};]+', re.S)
    stack = []
    current = []
    line = 1
    first_line = 1
    records = []
    for m in token_re.finditer(b.text):
        token = m.group()
        if token.startswith(("#", "/*")):
            line += token.count("\n")
            continue
        if token == "\n":
            if newline_leaves and current:
                records.append(([t for p, _ in stack for t in p] + current, first_line, any(x for _, x in stack)))
                current = []
            line += 1
        elif token == "{":
            inactive = "inactive:" in current
            current = [t for t in current if t != "inactive:"]
            if inactive:
                b.inactive_paths.append([t for p, _ in stack for t in p] + current)
            b.path_sources[tuple([t for p, _ in stack for t in p] + current)] = first_line
            path = [t for p, _ in stack for t in p] + current
            # An address container declares the address even when it has no leaves.
            if b.dialect.startswith("junos") and len(path) >= 4 and path[-4] == "family" and path[-3] in ("inet", "inet6") and path[-2] == "address":
                records.append((path, first_line, inactive or any(x for _, x in stack)))
            stack.append((current, inactive))
            current = []
        elif token in (";", "}"):
            if current:
                inactive = "inactive:" in current or any(x for _, x in stack)
                if "inactive:" in current:
                    b.inactive_paths.append([t for p, _ in stack for t in p] + [t for t in current if t != "inactive:"])
                records.append(([t for p, _ in stack for t in p] + [t for t in current if t != "inactive:"], first_line, inactive))
                current = []
            if token == "}":
                if stack:
                    stack.pop()
                else:
                    b.warn("unbalanced_braces", "Unmatched closing brace in configuration.", line)
        else:
            if not current:
                first_line = line
            current.append(token.strip('"'))
    if stack or current:
        b.warn("truncated_configuration", "Configuration ends with an unclosed block or unterminated statement.", line)
    return records


def _parse_junos(b):
    inactive_paths = []
    original = b.text
    b.text, scope = _junos_capture(b.text)
    if b.dialect == "junos_set":
        records = []
        for n, line in enumerate(b.text.splitlines(), 1):
            tokens = _tokens(line.strip())
            if tokens and tokens[0] == "set":
                records.append((tokens[1:], n, False))
            elif tokens and tokens[0] == "deactivate":
                inactive_paths.append(tokens[1:])
            elif tokens and tokens[0] in ("delete", "activate", "insert", "rename"):
                b.warn("configuration_delta", "Configuration command is a delta; supply a complete display set snapshot.", n)
    else:
        records = _hierarchy(b)
        inactive_paths.extend(b.inactive_paths)
    b.text = original

    def qualified(tokens):
        if tokens[:1] == ["interfaces"]:
            return tokens
        if tokens and re.fullmatch(_JUNOS_INTERFACE, tokens[0]):
            return ["interfaces", *tokens]
        if scope and tokens and tokens[0] in {"unit", "family", "address", "description", "disable", "vlan-id", "ether-options", "gigether-options"}:
            return [*scope, *tokens]
        return tokens

    records = [(qualified(tokens), n, inactive) for tokens, n, inactive in records]
    inactive_paths = [qualified(path) for path in inactive_paths]
    mappings = {}
    unresolved_members = set()
    inherited_paths = []
    lag_members = []
    for tokens, n, inactive in records:
        if "apply-groups" in tokens or "apply-groups-except" in tokens or (tokens and tokens[0] == "groups"):
            # Inherited values must not silently appear as complete effective facts.
            if "apply-groups" in tokens:
                inherited_paths.append(tokens[:tokens.index("apply-groups")])
            b.warn("unresolved_inheritance", "Junos groups/apply-groups are not expanded; affected effective configuration requires review.", n)
        if tokens[:2] == ["system", "host-name"] and len(tokens) > 2:
            b.hostname, b.hostname_line = tokens[2], n
        if len(tokens) >= 4 and tokens[0] == "routing-instances":
            name, statement = tokens[1], tokens[2:]
            context = b.context(name, n)
            if statement[0] == "interface":
                mappings[statement[1]] = name
                if inactive or any(tokens[:len(p)] == p for p in inactive_paths):
                    unresolved_members.add(statement[1])
                    b.warn("inactive_context_membership", "An inactive routing-instance membership needs effective-context review; this interface is excluded from inference.", n)
            elif statement[0] == "route-distinguisher":
                context["rd"] = statement[1]
            elif statement[0] == "vrf-target":
                context["rt"].append(" ".join(statement[1:]))
    for tokens, n, inactive in records:
        if len(tokens) < 3 or tokens[0] != "interfaces":
            continue
        if tokens[1] == "interface-range" or "<" in tokens[1] or "*" in tokens[1]:
            b.warn("unresolved_interface_range", "Junos interface-range/wildcard inheritance is not expanded; export effective interface configuration.", n)
            continue
        address_inactive = inactive or any(tokens[:len(p)] == p for p in inactive_paths)
        parent, rest = tokens[1], tokens[2:]
        base_prefix = ["interfaces", parent]
        base_inactive = any(base_prefix[:len(p)] == p for p in inactive_paths)
        base = b.iface(parent, n, inactive=base_inactive)
        name = parent
        if rest[:1] == ["unit"] and len(rest) >= 2:
            name = parent + "." + rest[1]
            rest = rest[2:]
        interface_prefix = ["interfaces", parent] + (["unit", name.rsplit(".", 1)[1]] if name != parent else [])
        interface_inactive = any(interface_prefix[:len(p)] == p for p in inactive_paths)
        iface = b.iface(name, n, mappings.get(name, "default"), interface_inactive)
        if name in mappings:
            iface["routing_context_id"] = b.context(mappings[name])["id"]
        if any(tokens[:len(p)] == p for p in inherited_paths):
            iface["effective_state"] = "unresolved"
        if name in unresolved_members:
            iface["effective_state"] = "unresolved"
        if not rest:
            continue
        if rest[0] == "description":
            iface["description"] = " ".join(rest[1:])
        elif rest[0] == "disable":
            iface["admin_state"] = "disabled"
        elif rest[0] == "vlan-id" and len(rest) > 1:
            iface["encapsulation"] = {"type": "dot1q", "vlan_id": rest[1]}
        elif "802.3ad" in rest:
            index = rest.index("802.3ad")
            if len(rest) > index + 1:
                lag_members.append((base, rest[index+1], n))
        elif rest[:1] == ["family"] and len(rest) >= 3 and rest[1] in ("inet", "inet6"):
            family = 4 if rest[1] == "inet" else 6
            cmd = rest[2:]
            if cmd[0] == "address" and len(cmd) > 1:
                value = cmd[1]
                role = "primary"
                if "virtual-address" in cmd:
                    v = cmd.index("virtual-address")
                    if len(cmd) > v+1:
                        prefix = value.split("/")[-1] if "/" in value else ("32" if family == 4 else "128")
                        b.address(iface, cmd[v+1] + "/" + prefix, n, family, "virtual", inactive=address_inactive)
                b.field_address(iface, value, n, family)
                if address_inactive:
                    for address in iface["addresses"]:
                        if any(ref["line"] == n for ref in address["source_refs"]):
                            address["effective_state"] = "inactive"
            elif cmd[0] in ("dhcp", "dhcp-client", "dhcpv6-client"):
                b.address(iface, None, n, family, "unknown", "dhcp", inactive=address_inactive)
            elif cmd[0] == "unnumbered-address":
                b.address(iface, None, n, family, "unknown", "unnumbered", inactive=address_inactive)
            elif cmd[0] in ("address", "unnumbered-address"):
                b.warn("unsupported_address_syntax", "Junos address statement is incomplete or unsupported.", n, iface)
    for member, lag_name, n in lag_members:
        if ("", lag_name) not in b.interfaces:
            b.warn("missing_aggregate_declaration", f"Aggregate {lag_name} is referenced by a member but has no parsed interface declaration.", n, member)
        lag = b.iface(lag_name, n)
        lag["aggregate_members"].append(member["id"])
        member["aggregate_interface_id"] = lag["id"]
    # Deactivation can be declared after the corresponding set statements.
    for iface in b.interfaces.values():
        parent, _, unit = iface["raw_name"].partition(".")
        prefix = ["interfaces", parent] + (["unit", unit] if unit else [])
        if any(prefix[:len(p)] == p for p in inactive_paths):
            iface["effective_state"] = "inactive"
        if any(prefix[:len(p)] == p for p in inherited_paths):
            iface["effective_state"] = "unresolved"
    _upstream_check(b, "\n".join(line for line in b.lines if line.startswith("set interfaces ")))


def _parse_nokia_classic(b):
    stack = []
    iface = None
    context = "default"
    ipv6_depth = None
    lag_ports = []
    for n, raw in enumerate(b.lines, 1):
        line = raw.strip()
        if not line or line.startswith(("#", "echo", "!")):
            continue
        words = _tokens(line)
        if not words:
            continue
        if line in ("exit all", "back", "exit"):
            if line == "exit all":
                stack = []
            elif stack:
                stack.pop()
            iface = next((x[1] for x in reversed(stack) if x[0] == "interface"), None)
            context = next((x[1] for x in reversed(stack) if x[0] in ("router", "vprn")), "default")
            ipv6_depth = next((k for k, x in enumerate(stack) if x[0] == "ipv6"), None)
            continue
        if words[0] == "configure":
            stack.append(("configure", None))
        elif words[0] == "system" and len(words) == 1:
            stack.append(("system", None))
        elif words[0] == "name" and len(words) > 1 and any(x[0] == "system" for x in stack):
            b.hostname, b.hostname_line = words[1], n
        elif words[0] == "router" and len(words) <= 3:
            context = words[1] if len(words) > 1 else "default"
            b.context(context, n)
            stack.append(("router", context))
            iface = None
        elif words[0] == "service" and len(words) == 1:
            stack.append(("service", None))
        elif words[0] == "lag" and len(words) > 1:
            lag = b.iface("lag-" + words[1], n)
            stack.append(("lag", lag))
            iface = None
        elif words[0] == "port" and len(words) > 1 and any(x[0] == "lag" for x in stack):
            lag = next(x[1] for x in reversed(stack) if x[0] == "lag")
            member = b.iface(words[1], n)
            member["kind"] = "physical"
            member["aggregate_interface_id"] = lag["id"]
            if member["id"] not in lag["aggregate_members"]:
                lag["aggregate_members"].append(member["id"])
        elif words[0] == "vprn" and len(words) > 1:
            context = "VPRN:" + words[1]
            b.context(context, n)
            stack.append(("vprn", context))
            iface = None
        elif words[0] == "interface" and len(words) > 1 and any(x[0] in ("router", "vprn") for x in stack):
            iface = b.iface(words[1], n, context)
            stack.append(("interface", iface))
        elif iface:
            if words[0] == "ipv6" and len(words) == 1:
                stack.append(("ipv6", None))
                ipv6_depth = len(stack)-1
            elif words[0] == "address" and len(words) > 1:
                if not any(x[0] in ("vrrp", "static-route", "bgp", "secondary-block") for x in stack):
                    family = 6 if ":" in words[1] or ipv6_depth is not None else 4
                    b.field_address(iface, " ".join(words[1:]), n, family)
            elif words[0] == "secondary" and len(words) > 1 and ("." in words[1] or ":" in words[1]):
                b.field_address(iface, " ".join(words[1:]) + " secondary", n, 6 if ":" in words[1] else 4)
            elif words[0] == "description":
                iface["description"] = " ".join(words[1:])
            elif words[0] in ("port", "sap") and len(words) > 1:
                iface["physical_port_refs"].append(words[1])
                if words[0] == "sap":
                    iface["encapsulation"] = {"type": "sap", "value": words[1]}
                    if "create" in words:
                        stack.append(("sap", None))
                if words[1].startswith("lag-"):
                    lag_ports.append((iface, words[1].split(":")[0], n))
            elif line == "shutdown":
                if stack[-1][0] == "interface":
                    iface["admin_state"] = "disabled"
            elif line == "no shutdown":
                if stack[-1][0] == "interface":
                    iface["admin_state"] = "enabled"
            elif words[0] == "vrrp" and len(words) > 1:
                stack.append(("vrrp", None))
            elif words[0] == "backup" and any(x[0] == "vrrp" for x in stack):
                primary = next((a for a in iface["addresses"] if a["family"] == 4 and a["prefix_length"] is not None), None)
                if len(words) > 1:
                    b.address(iface, words[1] + "/" + str(primary["prefix_length"] if primary else 32), n, 4, "virtual")
            elif words[0] in ("static-route", "bgp", "ospf", "isis"):
                stack.append((words[0], None))
            elif words[0] in ("unnumbered", "dhcp"):
                b.address(iface, None, n, 4, "unknown", words[0])
        elif words[0] in ("route-distinguisher", "vrf-target") and len(words) > 1:
            ctx = b.context(context)
            if words[0] == "route-distinguisher":
                ctx["rd"] = words[1]
            else:
                ctx["rt"].append(" ".join(words[1:]))
    if any(x[0] == "interface" for x in stack):
        b.warn("possibly_truncated_configuration", "Classic CLI ends inside an interface context; confirm the backup is complete.", len(b.lines))
    for iface, name, n in lag_ports:
        lag = b.iface(name, n)
        iface["parent_interface_id"] = lag["id"]


def _parse_nokia_md(b):
    records = []
    if b.dialect == "nokia_md_flat":
        for n, line in enumerate(b.lines, 1):
            tokens = _tokens(line)
            if tokens and tokens[0] in ("set", "/configure", "configure"):
                if tokens[0] == "set":
                    tokens = tokens[1:]
                if tokens and tokens[0] in ("/configure", "configure"):
                    records.append((tokens[1:], n, False))
            elif line.strip() and not line.strip().startswith(("#", "!", "echo")):
                b.warn("unsupported_md_relative_path", "Only complete absolute /configure paths are supported in flat MD-CLI exports.", n)
    else:
        for tokens, n, inactive in _hierarchy(b, newline_leaves=True):
            records.append((tokens[1:] if tokens and tokens[0] in ("configure", "/configure") else tokens, n, inactive))
    pending = {}
    for tokens, n, inactive in records:
        if len(tokens) == 3 and tokens[:2] == ["system", "name"]:
            b.hostname, b.hostname_line = tokens[2], n
        if len(tokens) >= 4 and tokens[0] == "lag" and tokens[2] == "port":
            lag_name = tokens[1] if tokens[1].startswith("lag-") else "lag-" + tokens[1]
            lag = b.iface(lag_name, n)
            member = b.iface(tokens[3], n)
            member["kind"] = "physical"
            member["aggregate_interface_id"] = lag["id"]
            if member["id"] not in lag["aggregate_members"]:
                lag["aggregate_members"].append(member["id"])
        if len(tokens) < 4:
            continue
        if tokens[0] == "router":
            context, offset = tokens[1], 2
        elif tokens[:2] == ["service", "vprn"] and len(tokens) > 4:
            context, offset = "VPRN:" + tokens[2], 3
        else:
            continue
        ctx = b.context(context, n)
        if tokens[offset:offset+1] == ["route-distinguisher"]:
            ctx["rd"] = tokens[-1]
        if tokens[offset:offset+1] != ["interface"] or len(tokens) <= offset+2:
            continue
        iface = b.iface(tokens[offset+1], n, context, inactive)
        rest = tokens[offset+2:]
        if not rest:
            continue
        if rest[0] == "description":
            iface["description"] = " ".join(rest[1:])
        elif rest[:1] == ["admin-state"]:
            iface["admin_state"] = {"enable": "enabled", "disable": "disabled"}.get(rest[-1], "unknown")
        elif rest[0] in ("port", "sap") and len(rest) > 1:
            iface["physical_port_refs"].append(rest[1])
            if rest[1].startswith("lag-"):
                lag = b.iface(rest[1].split(":")[0], n)
                iface["parent_interface_id"] = lag["id"]
            if rest[0] == "sap":
                iface["encapsulation"] = {"type": "sap", "value": rest[1]}
        elif rest[0] in ("ipv4", "ipv6"):
            family = 4 if rest[0] == "ipv4" else 6
            cmd = rest[1:]
            if not cmd:
                continue
            role = "secondary" if cmd[0] == "secondary" else "primary"
            if cmd[0] in ("dhcp", "dhcp-client", "autoconfigure"):
                b.address(iface, None, n, family, "unknown", "slaac" if cmd[0] == "autoconfigure" else "dhcp")
            elif cmd[0] in ("primary", "secondary", "address"):
                # MD primary { address 10.0.0.1; prefix-length 31; }
                # secondary 10.0.0.2 { prefix-length 31; }
                # ipv6 address 2001:db8::1 { prefix-length 64; }
                addr = None
                prefix = None
                leaf = None
                if len(cmd) > 1 and cmd[1] not in ("address", "prefix-length"):
                    addr = cmd[1]
                if "address" in cmd and cmd.index("address") + 1 < len(cmd):
                    addr = cmd[cmd.index("address")+1]
                    if addr == "prefix-length":
                        addr = None
                if "prefix-length" in cmd and cmd.index("prefix-length") + 1 < len(cmd):
                    prefix = cmd[cmd.index("prefix-length")+1]
                # Primary address and prefix are separate leaves with common key.
                key_addr = addr if cmd[0] in ("secondary", "address") else "primary"
                key = (iface["id"], family, cmd[0], key_addr)
                item = pending.setdefault(key, {"iface": iface, "family": family, "role": role, "line": n})
                if addr:
                    item["address"] = addr
                    # A keyed address container may precede its prefix leaf by
                    # several lines. Keep the actual declaration as first source.
                    source_path = tokens[:tokens.index(addr) + 1]
                    if b.dialect == "nokia_md_hierarchical":
                        item["line"] = b.path_sources.get(tuple(["configure"] + source_path), b.path_sources.get(tuple(["/configure"] + source_path), b.path_sources.get(tuple(source_path), n)))
                    else:
                        item["line"] = n
                if prefix:
                    item["prefix"] = prefix
                    item["prefix_line"] = n
                if addr and "/" in addr:
                    item["address"], item["prefix"] = addr.rsplit("/", 1)
            elif cmd[0] in ("vrrp", "unnumbered"):
                if cmd[0] == "unnumbered":
                    b.address(iface, None, n, family, "unknown", "unnumbered")
                else:
                    b.warn("unsupported_virtual_address", "MD-CLI VRRP requires explicit virtual-address mapping and is not expanded.", n, iface)
    for item in pending.values():
        if "address" in item and "prefix" in item:
            b.address(item["iface"], item["address"] + "/" + item["prefix"], item["line"], item["family"], item["role"])
            if item.get("prefix_line") and item["prefix_line"] != item["line"] and item["iface"]["addresses"]:
                item["iface"]["addresses"][-1]["source_refs"].append(b.ref(item["prefix_line"]))
        else:
            b.warn("incomplete_md_address", "MD-CLI address is missing a supported address/prefix-length pair.", item["line"], item["iface"])


def parse_config(text: str, filename: str, source_file_id: str, device_id: str, dialect: str | None = None) -> dict:
    """Return normalized facts and honest per-file diagnostics; no filesystem I/O."""
    # Strip terminal escapes without deleting lines, preserving source coordinates.
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text.lstrip("\ufeff")).replace("\r\n", "\n").replace("\r", "\n")
    clean = clean.replace("---- More ----", "").replace("--More--", "")
    dialect = _ALIASES.get(dialect, dialect) if dialect else detect_dialect(clean)
    b = _Builder(clean, filename, source_file_id, device_id, dialect)
    if not clean.strip():
        b.warn("empty_file", "Configuration file is empty.", severity="error")
    elif dialect not in _DIALECTS:
        b.warn("unsupported_dialect", "Configuration dialect is unknown or unsupported. Choose a supported parser or supply a full configuration.", severity="error")
    else:
        try:
            if dialect.startswith(("cisco", "huawei")):
                _parse_cli(b)
            elif dialect.startswith("junos"):
                _parse_junos(b)
            elif dialect == "nokia_classic":
                _parse_nokia_classic(b)
            else:
                _parse_nokia_md(b)
        except Exception as exc:
            b.warn("parser_error", f"Parser could not complete this file ({type(exc).__name__}). Parsed facts are retained for review.", severity="error")
        if re.search(r"(?mi)truncated|output omitted|-- ?more ?--", clean):
            b.warn("truncated_configuration", "Source contains a truncation or pagination marker; verify completeness.")
        if len(re.findall(r'(?<!\\)"', clean)) % 2:
            b.warn("unterminated_quote", "Configuration contains an unmatched quote; verify backup completeness.")
    result = b.finish()
    if not clean.strip() or dialect not in _DIALECTS:
        result["status"] = result["device"]["parse_status"] = "failed"
    return result
