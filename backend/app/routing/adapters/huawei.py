"""Huawei VRP IPv4 IGP adapter with explicit ignored-extension diagnostics.

Defaults are limited to documented VRP behavior, not guessed operational speed.
See docs/ROUTING.md for command references and accepted configuration scope.
"""
import ipaddress
import re

from ..model import Advertisement, Binding, Instance, Issue, RoutingFacts, SourceRef, identity, IGNORED_SEMANTIC_CODES
from .semantics import classify_statement

VERSION = "1.0.0"


def extract(text: str, parsed: dict) -> RoutingFacts:
    device = parsed["device"]["id"]
    source = parsed["device"]["source_file_id"]
    facts = RoutingFacts(adapter_versions={"huawei_vrp_igp": VERSION}, adapter_devices=[device])
    interfaces = {i["raw_name"].lower(): i for i in parsed["interfaces"]}
    contexts = {c["local_name"]: c["id"] for c in parsed["contexts"]}
    blocks, current = [], None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line in ("#", "!", "return", "quit"):
            current = None
        elif re.match(r"^(?:ospf|isis)(?:\s|$)|^interface\s", line) and not raw[:1].isspace():
            current = [n, line, []]
            blocks.append(current)
        elif current and raw[:1].isspace() and line:
            current[2].append((n, line))
        elif raw and not raw[:1].isspace():
            current = None

    def ref(n):
        return SourceRef(source_file_id=source, line=n)

    def issue(code, message, n, instance=None, interface=None, protocol=None, statement=None):
        if statement is not None and code in IGNORED_SEMANTIC_CODES:
            code,message=classify_statement(statement,code,message)
        facts.issues.append(Issue(code=code, message=message, device_id=device,
                                 instance_id=instance.id if instance else None,
                                 protocol=instance.protocol if instance else protocol,
                                 interface_id=interface["id"] if interface else None, blocking=code not in IGNORED_SEMANTIC_CODES, source_refs=[ref(n)]))

    instances, configs = {}, {}
    for n, header, lines in blocks:
        m = re.fullmatch(r"(ospf|isis)(?:\s+(\d+))?((?:\s+(?:router-id\s+[\d.]+|vpn-instance\s+\S+))*)", header)
        if not m:
            continue
        protocol, process, rest = m.group(1), m.group(2) or "1", m.group(3)
        vpn = re.search(r"\bvpn-instance\s+(\S+)", rest)
        context = contexts.get(vpn.group(1) if vpn else "default")
        if not context:
            issue("missing_context", "协议引用的 VPN 实例未解析。", n)
            continue
        inst = Instance(id=identity(device, protocol, process, context), device_id=device,
                        routing_context_id=context, protocol=protocol, local_name=process,
                        source_refs=[ref(n)], levels=["1", "2"] if protocol == "isis" else [])
        inst.defaults_profile='huawei_vrp_igp/defaults-1'
        rid = re.search(r"\brouter-id\s+([\d.]+)", rest)
        inst.router_id = rid.group(1) if rid else None
        key = (protocol, process, context)
        if key in instances:
            issue("duplicate_instance", "同一协议实例重复声明，需提供完整配置。", n, instances[key])
            continue
        instances[key] = inst
        facts.instances.append(inst)
        cfg = {"networks": [], "silent": set(), "unsilent": set(), "silent_all": False,
               "costs": {}, "style": "narrow"}
        configs[inst.id] = cfg
        area = None
        for ln, line in lines:
            if protocol == "ospf" and (a := re.fullmatch(r"area\s+([\d.]+)", line)):
                try:
                    area = str(ipaddress.IPv4Address(int(a[1]) if a[1].isdigit() else a[1]))
                except (ValueError, ipaddress.AddressValueError):
                    issue("invalid_area", "OSPF Area ID 无效。", ln, inst)
                if area and area not in inst.areas:
                    inst.areas.append(area)
            elif protocol == "ospf" and (m := re.fullmatch(r"network\s+([\d.]+)\s+([\d.]+)", line)):
                try:
                    cfg["networks"].append((area, int(ipaddress.IPv4Address(m[1])),
                                            int(ipaddress.IPv4Address(m[2])), ln))
                    if area is None:
                        issue("missing_area", "network 缺少所属 Area。", ln, inst)
                except ValueError:
                    issue("invalid_network", "OSPF network 地址或通配符无效。", ln, inst)
            elif m := re.fullmatch(r"maximum load-balancing\s+(\d+)", line):
                value = int(m[1])
                inst.forwarding_mode='single_next_hop' if value==1 else 'ecmp'
                if 1 <= value <= 256:
                    inst.max_paths = value
                else:
                    issue("invalid_ecmp_limit", "ECMP 上限超出支持范围。", ln, inst)
            elif protocol == "ospf" and (m := re.fullmatch(r"(undo )?silent-interface\s+(.+)", line)):
                name = m[2].replace(" ", "").lower()
                if name == "all":
                    cfg["silent_all"] = not bool(m[1])
                else:
                    cfg["unsilent" if m[1] else "silent"].add(name)
            elif protocol == "isis" and (m := re.fullmatch(r"is-level\s+(level-1-2|level-1|level-2)", line)):
                inst.levels = ["1", "2"] if m[1] == "level-1-2" else [m[1][-1]]
            elif protocol == "isis" and (m := re.fullmatch(r"network-entity\s+([\da-fA-F.]+)", line)):
                net = m[1].replace(".", "").lower()
                if len(net) < 16 or len(net) > 40 or len(net) % 2 or not net.endswith("00"):
                    issue("invalid_net", "IS-IS NET 格式无效。", ln, inst)
                else:
                    if inst.router_id and inst.router_id != net[-14:-2]:
                        issue("multiple_system_ids", "同一实例配置了不同 System ID。", ln, inst)
                    inst.router_id = net[-14:-2]
                    if net[:-14] not in inst.areas:
                        inst.areas.append(net[:-14])
            elif protocol == "isis" and (m := re.fullmatch(r"cost-style\s+(wide|narrow)", line)):
                cfg["style"] = m[1]
            elif protocol == "isis" and (m := re.fullmatch(r"circuit-cost\s+(\d+)(?:\s+level-([12]))?", line)):
                for level in [m[2]] if m[2] else ["1", "2"]:
                    cfg["costs"][level] = (int(m[1]), ln)
            elif line.startswith(("description ", "bandwidth-reference ")) or line == "opaque-capability enable":
                pass
            else:
                issue("unsupported_protocol_command", f"{protocol.upper()} 存在尚未支持的配置语义，请查看来源行。", ln, inst, statement=line)
        if protocol == "isis" and not inst.router_id:
            issue("missing_net", "IS-IS 缺少有效 network-entity。", n, inst)
        if protocol == "isis":
            inst.metric_style = cfg["style"]

    interface_blocks = {header.split(maxsplit=1)[1].lower(): (n, lines)
                        for n, header, lines in blocks if header.startswith("interface ")}
    for name, iface in interfaces.items():
        n, lines = interface_blocks.get(name, (1, []))
        options = {"ospf": {}, "isis": {"costs": {}}}
        explicit_ospf, isis_process = None, None
        unsupported = {"ospf": [], "isis": []}
        for ln, line in lines:
            if m := re.fullmatch(r"ospf enable(?:\s+(\d+))?\s+area\s+([\d.]+)", line):
                try:
                    area = str(ipaddress.IPv4Address(int(m[2]) if m[2].isdigit() else m[2]))
                    explicit_ospf = (m[1] or "1", area, ln)
                except ValueError:
                    issue("invalid_area", "显式 OSPF 接口 Area 无效。", ln, interface=iface, protocol="ospf")
            elif m := re.fullmatch(r"ospf cost\s+(\d+)", line):
                options["ospf"]["cost"] = (int(m[1]), ln)
            elif m := re.fullmatch(r"ospf network-type\s+(\S+)", line):
                options["ospf"]["network_type"] = (m[1], ln)
            elif m := re.fullmatch(r"ospf timer (hello|dead)\s+(\d+)", line):
                options["ospf"][m[1]] = int(m[2])
            elif m := re.fullmatch(r"isis enable(?:\s+(\d+))?", line):
                isis_process = (m[1] or "1", ln)
            elif m := re.fullmatch(r"isis cost\s+(\d+)(?:\s+level-([12]))?", line):
                for level in [m[2]] if m[2] else ["1", "2"]:
                    options["isis"]["costs"][level] = (int(m[1]), ln)
            elif m := re.fullmatch(r"isis circuit-level\s+(level-1-2|level-1|level-2)", line):
                options["isis"]["levels"] = ["1", "2"] if m[1] == "level-1-2" else [m[1][-1]]
            elif line == "isis circuit-type p2p":
                options["isis"]["network_type"] = ("p2p", ln)
            elif line == "isis silent":
                options["isis"]["passive"] = True
            elif line.startswith(("ospf ", "undo ospf ")):
                unsupported["ospf"].append((ln,line))
            elif line.startswith(("isis ", "undo isis ")):
                unsupported["isis"].append((ln,line))

        selected = []
        context = iface["routing_context_id"]
        if explicit_ospf:
            proc, area, ln = explicit_ospf
            inst = instances.get(("ospf", proc, context))
            if inst:
                selected.append((inst, area, ln))
            else:
                issue("missing_instance", "接口启用的 OSPF 实例未解析。", ln, interface=iface, protocol="ospf")
        else:
            for inst in facts.instances:
                if inst.protocol != "ospf" or inst.routing_context_id != context:
                    continue
                matches = []
                for area, net, wildcard, ln in configs[inst.id]["networks"]:
                    mask = 0xffffffff ^ wildcard
                    if area and any(a.get("family") == 4 and a.get("address") and a.get("role") == "primary"
                                    and int(ipaddress.IPv4Address(a["address"])) & mask == net & mask
                                    for a in iface["addresses"]):
                        matches.append((area, ln))
                if len({a for a, _ in matches}) > 1:
                    issue("ambiguous_area", "接口匹配多个 Area，需使用明确的 ospf enable 配置。", n, inst, iface)
                elif matches:
                    selected.append((inst, *matches[0]))
        if sum(i.protocol == "ospf" for i, _, _ in selected) > 1:
            for inst, _, ln in selected:
                issue("ambiguous_process", "接口匹配多个 OSPF 进程。", ln, inst, iface)
        if isis_process:
            proc, ln = isis_process
            inst = instances.get(("isis", proc, context))
            if inst:
                levels = options["isis"].get("levels", ["1", "2"]) if len(inst.levels) == 2 else inst.levels
                selected.extend((inst, level, ln) for level in levels if level in inst.levels)
            else:
                issue("missing_instance", "接口启用的 IS-IS 实例未解析。", ln, interface=iface, protocol="isis")

        for proto in ("ospf", "isis"):
            if not any(inst.protocol == proto for inst, _, _ in selected):
                for badline,statement in unsupported[proto]:
                    if re.match(r"(?:undo )?(?:ospf|isis) enable\b",statement):
                        issue("unresolved_protocol_interface", f"{proto.upper()} 接口启用配置无法关联到协议实例。", badline, interface=iface, protocol=proto)
                    else:
                        issue("unsupported_interface_command", f"{proto.upper()} 接口扩展语义未参与基础图计算。", badline, interface=iface, protocol=proto,statement=statement)

        for inst, scope, ln in selected:
            proto, cfg = inst.protocol, configs[inst.id]
            opt = options[proto]
            loopback = iface["kind"] == "loopback"
            kind = "loopback" if loopback else "broadcast" if re.match(r"(?:.*ethernet|eth-trunk|vlanif|[124]0?0?ge|ge)\d", name) else "unsupported"
            network_type, type_line = opt.get("network_type", (kind, ln))
            if network_type not in ("p2p", "broadcast", "loopback"):
                network_type = "unsupported"
                issue("unsupported_network_type", "接口网络类型无法确定或暂不支持。", type_line, inst, iface)
            if proto == "ospf":
                cost, cost_line = opt.get("cost", (0 if loopback else None, ln))
                explicit = "cost" in opt
                passive = loopback or ((cfg["silent_all"] or name in cfg["silent"]) and name not in cfg["unsilent"])
                if scope not in inst.areas:
                    inst.areas.append(scope)
            else:
                explicit = scope in opt["costs"] or scope in cfg["costs"]
                cost, cost_line = opt["costs"].get(scope, cfg["costs"].get(scope, (0 if loopback else 10, ln)))
                passive = loopback or opt.get("passive", False)
            upper = 65535 if proto == "ospf" else 63 if cfg["style"] == "narrow" else 16777214
            if cost is not None and (cost > upper or cost < (0 if loopback else 1)):
                issue("invalid_cost", "接口开销超出当前协议支持范围。", cost_line, inst, iface)
                cost = None
            binding = Binding(id=identity(inst.id, iface["id"], scope), device_id=device, instance_id=inst.id,
                              interface_id=iface["id"], scope=scope, passive=passive, network_type=network_type,
                              cost=cost, metric_limit=upper, cost_origin="explicit" if explicit else "profile_default" if cost is not None else "unknown",
                              hello=opt.get("hello", 10) if proto == "ospf" else None,
                              dead=opt.get("dead", 40) if proto == "ospf" else None,
                              source_refs=list({r.line: r for r in [ref(ln), ref(cost_line), ref(type_line)]}.values()))
            facts.bindings.append(binding)
            for badline,statement in unsupported[proto]:
                issue("unsupported_interface_command", f"{proto.upper()} 接口存在尚未支持的配置语义。", badline, inst, iface, statement=statement)
            for address in iface["addresses"]:
                if address.get("family") != 4 or not address.get("address") or address.get("role") in ("virtual", "anycast", "link_local"):
                    continue
                prefix = f"{address['address']}/32" if proto == "ospf" and loopback and network_type == "loopback" else address.get("canonical_network")
                if prefix:
                    facts.advertisements.append(Advertisement(id=identity(binding.id, address["id"]), device_id=device,
                        binding_id=binding.id, interface_id=iface["id"], address_id=address["id"],
                        address=address["address"], prefix=prefix,
                        source_refs=[SourceRef(source_file_id=r["source_file_id"], line=r["line"]) for r in address["source_refs"]]))
    for diagnostic in parsed.get("diagnostics", []):
        if diagnostic.get("code") in {"truncated_configuration", "unterminated_quote", "parser_error", "unsupported_address_syntax", "unsupported_interface_declaration"}:
            issue("incomplete_configuration", "配置存在截断或未解析的接口语义，无法保证路由拓扑完整。", diagnostic.get("line") or 1)
    return RoutingFacts.model_validate(facts.model_dump())
