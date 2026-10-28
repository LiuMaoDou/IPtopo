"""Offline single-area/single-level IPv4 SPF and per-hop ideal ECMP.

Only consumes the versioned neutral contract. No vendor names, parsers or
platform tie-breaks belong here. Candidate adjacency is configuration evidence.
"""
from collections import defaultdict, deque
import ipaddress
from itertools import combinations, product

from .model import SimulationRequest, identity, validate_snapshot_routing
from .spf import calculate

ENGINE_VERSION = "1.1.0"


def simulate(snapshot: dict, request: SimulationRequest) -> dict:
    facts = validate_snapshot_routing(snapshot)
    instances = {i.id: i for i in facts.instances if i.protocol == request.protocol}
    interfaces = {i["id"]: i for i in snapshot.get("interfaces", [])}
    devices = {d["id"]: d for d in snapshot.get("devices", [])}
    bindings = {b.id: b for b in facts.bindings if b.instance_id in instances and b.scope == request.scope}
    all_bindings = {b.id: b for b in facts.bindings}
    adverts = {a.id: a for a in facts.advertisements}
    source = instances.get(request.source_instance_id)
    target_ad = adverts.get(request.destination_advertisement_id)
    if not source or not target_ad or target_ad.binding_id not in bindings:
        raise ValueError("源实例或目的地址不属于所选协议 / Area / Level。")
    target_binding = bindings[target_ad.binding_id]
    target = instances[target_binding.instance_id]
    if ipaddress.ip_network(target_ad.prefix).prefixlen != 32 or interfaces.get(target_ad.interface_id, {}).get("kind") != "loopback":
        raise ValueError("首版目的地址需为所选协议发布的 /32 Loopback。")
    if not any(b.instance_id == source.id for b in bindings.values()):
        raise ValueError("源实例没有所选 Area / Level 的接口。")
    scenario = request.scenario
    if set(scenario.disabled_interfaces) - interfaces.keys():
        raise ValueError("场景包含不存在的接口。")
    for bid, value in scenario.metric_overrides.items():
        if bid not in bindings or isinstance(value, bool) or not 1 <= value <= bindings[bid].metric_limit:
            raise ValueError("开销覆盖必须引用所选范围内的协议接口，且开销为有效正整数。")
    if scenario.unknown_cost is not None and any(b.cost is None and scenario.unknown_cost > b.metric_limit for b in bindings.values()):
        raise ValueError("未知开销假设超过当前协议的度量范围。")
    result = {
        "engine_version": ENGINE_VERSION, "snapshot_id": snapshot.get("id"),
        "protocol": request.protocol, "scope": request.scope,
        "source_device_id": source.device_id, "destination_device_id": target.device_id,
        "destination_address": target_ad.address, "destination_prefix": target_ad.prefix,
        "status": "unreachable", "total_cost": None, "path_count": "0", "paths": [],
        "paths_truncated": False, "hops": [], "forwarding": [], "diagnostics": [],
        "assumptions": ["配置推演：假设候选连接的物理链路和协议邻接正常。",
                        "仅计算所选协议、单 Area / Level 的内部路由，未模拟跨协议 RIB/FIB 竞争及 ACL。",
                        "占比假设每台设备对等价下一跳均匀分担，Hash 独立且流量充分；不是实测带宽。"],
        "forwarding_status": "theoretical", "scenario": scenario.model_dump(),
    }
    diagnostics = result["diagnostics"]

    def diag(code, message, **details):
        diagnostics.append({"code": code, "message": message, **details})

    def active(b):
        iface = interfaces.get(b.interface_id)
        return bool(iface and b.interface_id not in scenario.disabled_interfaces
                    and iface.get("admin_state") != "disabled" and iface.get("effective_state") == "active"
                    and iface.get("routing_context_id") == instances[b.instance_id].routing_context_id
                    and not iface.get("aggregate_interface_id"))

    def cost(b):
        return scenario.metric_overrides.get(b.id, b.cost if b.cost is not None else scenario.unknown_cost)

    # Include all enabled protocol bindings in connectivity validation, even if a
    # cost is unknown. Otherwise removing an unknown cheaper path fabricates SPF.
    by_interface = defaultdict(list)
    for binding in bindings.values():
        if active(binding) and not binding.passive and binding.network_type != "loopback":
            by_interface[binding.interface_id].append(binding)
    pairs, segments = [], defaultdict(dict)
    for link in snapshot.get("links", []):
        if link.get("review_state") == "rejected" or 4 not in link.get("families", []):
            continue
        a, b = link.get("interface_a_id"), link.get("interface_b_id")
        if link.get("relation_type") == "segment_member":
            if a in by_interface:
                segments[link["device_b_id"]][a] = link["id"]
        elif a in by_interface and b in by_interface:
            pairs.append((a, b, [link["id"]]))
    for segment, members in segments.items():
        if len(members) > 128:
            result["status"] = "unsupported"
            diag("segment_limit", "共享网段超过 128 个协议接口，当前版本停止展开。", segment_id=segment)
            return result
        for a, b in combinations(members, 2):
            pairs.append((a, b, [members[a], members[b]]))
    arcs, neighbors, seen = [], defaultdict(set), set()
    uncertain_encoding=set()

    def gateway(a, b):
        for own in interfaces[a.interface_id].get("addresses", []):
            if own.get("family") != 4 or not own.get("canonical_network") or own.get("effective_state") != "active":
                continue
            for peer in interfaces[b.interface_id].get("addresses", []):
                if peer.get("family") == 4 and peer.get("effective_state") == "active" and peer.get("role") == "primary" and peer.get("address"):
                    if ipaddress.ip_address(peer["address"]) in ipaddress.ip_network(own["canonical_network"]):
                        return peer["address"]
        return None

    for ia, ib, link_ids in pairs:
        for a, b in product(by_interface[ia], by_interface[ib]):
            if a.device_id == b.device_id:
                continue
            left, right = instances[a.instance_id], instances[b.instance_id]
            left_style = left.level_metric_styles.get(request.scope, left.metric_style)
            right_style = right.level_metric_styles.get(request.scope, right.metric_style)
            reason = None
            if a.network_type != b.network_type or a.network_type == "unsupported":
                reason = "两端网络类型不匹配或不支持。"
            elif request.protocol == "isis" and request.scope == "1" and not set(left.areas).intersection(right.areas):
                reason = "IS-IS Level-1 两端 Area 地址不匹配。"
            elif request.protocol == "isis" and left_style != right_style and "both" not in (left_style, right_style):
                uncertain_encoding.update((a.instance_id,b.instance_id))
                reason = "IS-IS 两端收发度量编码的互通语义尚未确定，不能据此断言邻接或路由可用。"
            elif request.protocol=='isis' and 'narrow' in (left_style,right_style) and 'both' in (left_style,right_style) and any(v is not None and v>63 for v in (cost(a),cost(b))):
                uncertain_encoding.update((a.instance_id,b.instance_id))
                reason = '双编码接口的宽度量超过 63，不能推定对端接收的窄度量值。'
            elif request.protocol == "ospf" and (a.hello, a.dead) != (b.hello, b.dead):
                reason = "OSPF Hello / Dead 定时器不匹配。"
            if reason:
                diag("incompatible_adjacency", reason, interface_ids=[ia, ib])
                continue
            ga, gb = gateway(a, b), gateway(b, a)
            if not ga or not gb:
                diag("missing_ip_adjacency", "缺少可验证的双向 IPv4 下一跳；首版仅支持编号的 IPv4 连接。", interface_ids=[ia, ib])
                continue
            for origin, dest, gw in ((a, b, ga), (b, a, gb)):
                key = (origin.id, dest.id)
                if key in seen:
                    continue
                seen.add(key)
                neighbors[origin.instance_id].add(dest.instance_id)
                arcs.append({"id": identity(*key), "source": origin.instance_id, "target": dest.instance_id,
                             "source_device_id": origin.device_id, "target_device_id": dest.device_id,
                             "interface_id": origin.interface_id, "peer_interface_id": dest.interface_id,
                             "binding_id": origin.id, "next_hop": gw, "cost": cost(origin), "link_ids": link_ids,
                             "source_refs": [r.model_dump() for r in origin.source_refs]})
            if len(arcs) > 100000:
                result["status"] = "unsupported"
                diag("arc_limit", "协议拓扑超过当前 100,000 条有向邻接的计算上限。")
                return result
    component, pending = {source.id}, deque([source.id])
    while pending:
        for neighbor in neighbors[pending.popleft()]:
            if neighbor not in component:
                component.add(neighbor)
                pending.append(neighbor)
    if component.intersection(uncertain_encoding):
        result['status']='unsupported'
        diag('metric_encoding_unknown','当前范围存在未确定的 IS-IS 窄/宽度量互通语义；请提供收发策略、平台版本或详细 LSDB。')
        return result
    component_devices = {instances[n].device_id for n in component}
    blocking = False
    covered_devices = set(facts.adapter_devices)
    boundaries = set()
    all_segments = defaultdict(set)
    for ia, ib, _ in pairs:
        for origin, peer in ((ia, ib), (ib, ia)):
            own_device, peer_device = interfaces[origin]["device_id"], interfaces[peer]["device_id"]
            if own_device in component_devices and peer_device not in covered_devices:
                boundaries.add(peer_device)
    # Also inspect links whose far endpoint has no protocol binding at all.
    for link in snapshot.get("links", []):
        if link.get("review_state") == "rejected" or 4 not in link.get("families", []):
            continue
        if link.get("relation_type") == "segment_member" and link.get("interface_a_id") in interfaces:
            all_segments[link["device_b_id"]].add(link["interface_a_id"])
        for a, b in (("a", "b"), ("b", "a")):
            own, peer = link.get(f"interface_{a}_id"), link.get(f"interface_{b}_id")
            if own in by_interface and peer in interfaces:
                if any(binding.instance_id in component for binding in by_interface[own]) and interfaces[peer]["device_id"] not in covered_devices:
                    boundaries.add(interfaces[peer]["device_id"])
    for members in all_segments.values():
        if any(b.instance_id in component for iid in members for b in by_interface[iid]):
            boundaries.update(interfaces[iid]["device_id"] for iid in members if interfaces[iid]["device_id"] not in covered_devices)
    if boundaries:
        diag("unparsed_peer", "候选连接的对端缺少统一路由事实；请重新导入支持的完整配置。", device_ids=sorted(boundaries))
        blocking = True
    for item in facts.issues:
        relevant = item.instance_id in component or (item.instance_id is None and item.device_id in component_devices)
        if relevant and item.blocking and item.protocol in (None, request.protocol):
            diag(item.code, item.message, device_id=item.device_id, interface_id=item.interface_id,
                 source_refs=[r.model_dump() for r in item.source_refs])
            blocking = True
    for arc in arcs:
        if arc["source"] in component and (arc["cost"] is None or arc["cost"] <= 0):
            diag("unknown_cost", "邻接出口开销未知；请配置显式开销或填写场景中的未知开销假设。", interface_id=arc["interface_id"])
            blocking = True
    point_to_point_peers = defaultdict(set)
    for arc in arcs:
        if arc["source"] in component and bindings[arc["binding_id"]].network_type == "p2p":
            point_to_point_peers[arc["binding_id"]].add(arc["peer_interface_id"])
    for bid, peers in point_to_point_peers.items():
        if len(peers) > 1:
            diag("ambiguous_p2p", "P2P 接口匹配了多个候选对端，需先修正拓扑关系。", interface_id=bindings[bid].interface_id)
            blocking = True
    identifiers = defaultdict(list)
    for iid in component:
        inst = instances[iid]
        if inst.router_id:
            identifiers[inst.router_id].append(iid)
    if any(len(ids) > 1 for ids in identifiers.values()):
        diag("duplicate_router_id", "连通范围内存在重复 Router ID / System ID。")
        blocking = True
    for ad in facts.advertisements:
        b = all_bindings.get(ad.binding_id)
        if b and b.instance_id in component and ad.address == target_ad.address and ad.device_id != target.device_id:
            diag("duplicate_destination", "目的地址由多台设备发布，无法作为唯一设备地址计算。")
            blocking = True
    if blocking:
        result["status"] = "incomplete"
        return result
    if not active(target_binding):
        diag("destination_inactive", "目的 Loopback 被关闭、归属已改变或在场景中退出。")
        return result
    current_address = next((a for a in interfaces[target_ad.interface_id].get("addresses", []) if a["id"] == target_ad.address_id), None)
    if not current_address or current_address.get("address") != target_ad.address or current_address.get("effective_state") != "active":
        result["status"] = "incomplete"
        diag("stale_advertisement", "目的地址已被修正或失效，请重新导入配置以生成路由事实。")
        return result
    terminal_cost = cost(target_binding)
    if terminal_cost is None:
        result["status"] = "incomplete"
        diag("unknown_destination_cost", "目的前缀的发布开销未知。")
        return result
    if target.id not in component:
        diag("no_path", "所选 Area / Level 内没有可用路径；跨 Area / Level 选路不在首版范围内。")
        return result
    calculated = calculate([a for a in arcs if a["source"] in component], source.id, target.id,
        terminal_cost=terminal_cost, path_limit=request.path_limit,
        nodes={i.id: {"device_id": i.device_id, "label": devices[i.device_id]["hostname"], "max_paths": i.max_paths,
                      "forwarding_mode": i.forwarding_mode,
                      "metric_path_limit": i.metric_path_limit if i.metric_path_limit is not None else 1023 if request.protocol=='isis' and i.level_metric_styles.get(request.scope,i.metric_style)=='narrow' else None}
               for i in instances.values()})
    diagnostics.extend(calculated.pop("diagnostics"))
    result.update(calculated)
    if scenario.unknown_cost is not None:
        result["assumptions"].append(f"场景假设：未知接口开销取 {scenario.unknown_cost}，未修改原配置。")
    if any(instances[n].max_paths is None for n in component):
        result["assumptions"].append("部分设备的 ECMP 上限未知，结果展示全部协议候选下一跳。")
    if any(instances[n].forwarding_mode=='single_next_hop' for n in component):
        result['assumptions'].append('部分设备有效转发策略为单下一跳；协议候选路径和理论分担不代表这些设备默认安装的 FIB。')
    if any(instances[n].router_id is None for n in component):
        result["assumptions"].append("部分 Router ID 未显式配置，假设各设备自动选择的 ID 唯一。")
    return result
