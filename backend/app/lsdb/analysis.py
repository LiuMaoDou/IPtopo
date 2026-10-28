"""Observation selection, identity correlation and neutral protocol graph construction."""
from collections import defaultdict
from copy import deepcopy
from itertools import combinations
from ..routing.model import identity, validate_snapshot_routing
from ..routing.spf import calculate
from ..routing.simulation import simulate
from ..routing.model import SimulationRequest
from .model import Batch, AnalyzeRequest


def select_records(batch, diagnostics):
    groups = defaultdict(list)
    for r in batch.records:
        groups[r.key].append(r)
    selected = []
    for key, versions in groups.items():
        if all(r.kind == "other" for r in versions):
            continue
        def sequence(r):
            return r.sequence - 2**32 if batch.protocol=="ospf" and r.sequence >= 2**31 else r.sequence
        seq = max(map(sequence,versions))
        candidates = [r for r in versions if sequence(r)==seq]
        if batch.protocol=="ospf":
            checksum=max(r.checksum for r in candidates)
            # Some summary formats omit the checksum (represented as zero).
            # Keep those headers for aging checks, but never prefer an older
            # detailed sequence merely because it has a body.
            candidates=[r for r in candidates if r.checksum==checksum or not r.detailed and r.checksum==0]
            expired=[r for r in candidates if r.expired or r.age>=3600]
            if expired: candidates=expired
            elif max(r.age for r in candidates)-min(r.age for r in candidates)>900:
                age=min(r.age for r in candidates); candidates=[r for r in candidates if r.age==age]
        elif len({r.checksum for r in candidates})>1:
            diagnostics.append(dict(code="version_conflict",message=f"{key} 相同序号但校验和不同，不能确定有效版本。",blocking=True));continue
        detailed = [r for r in candidates if r.detailed]
        if detailed:
            candidates = detailed
        elif not any(r.expired or (batch.protocol == "ospf" and r.age >= 3600) or (batch.protocol == "isis" and r.age == 0) for r in candidates):
            diagnostics.append(dict(code="summary_only", message=f"{key} 最新版本只有摘要记录，不能恢复其邻接。", blocking=True, source_refs=[r.model_dump() for r in candidates[0].source_refs]))
        def content(r):
            d=r.model_dump(exclude={"source_refs","age"})
            for x in d["links"]+d["prefixes"]: x.pop("source_refs",None)
            return d
        if any(content(r)!=content(candidates[0]) for r in candidates[1:]):
            diagnostics.append(dict(code="version_conflict",message=f"{key} 同版本记录内容冲突，请分开不同采集时刻。",blocking=True));continue
        r=candidates[0]
        if r.expired or (batch.protocol=="ospf" and r.age>=3600) or (batch.protocol=="isis" and r.age==0):
            diagnostics.append(dict(code="expired_record",message=f"{key} 已到 MaxAge / Holdtime 0，未用于拓扑。",blocking=False));continue
        if r.kind!="other" and r.detailed:
            selected.append(r)
    return selected


def build(batch: Batch, snapshot, request: AnalyzeRequest):
    request = request.model_copy(update={"mappings": request.mappings or {}})
    diagnostics = [d.model_dump() for d in batch.diagnostics]
    def diag(code,message,blocking=True,refs=None):
        diagnostics.append(dict(code=code,message=message,blocking=blocking,source_refs=refs or []))
    records=select_records(batch,diagnostics)
    facts=validate_snapshot_routing(snapshot)
    instances={i.id:i for i in facts.instances if i.protocol==batch.protocol}
    devices={d["id"]:d for d in snapshot.get("devices",[])}
    interfaces={i["id"]:i for i in snapshot.get("interfaces",[])}
    contexts={c["id"]:c for c in snapshot.get("contexts",[])}
    by_origin=defaultdict(list)
    for r in records: by_origin[r.origin].append(r)
    def pseudo(origin):
        return origin.startswith("net:") if batch.protocol=="ospf" else origin[-2:]!="00"
    def router_id(origin):
        return origin if batch.protocol=="ospf" else origin[:-3]
    def match_id(value):
        return value.lower().replace(".", "") if batch.protocol=="isis" else value
    if batch.protocol=="isis":
        for origin,rs in by_origin.items():
            fragments={r.fragment for r in rs}
            if 0 not in fragments or fragments != set(range(max(fragments)+1)):
                diag("missing_fragment",f"{origin} 缺少 LSP 零号或中间分片。")
    origins=set(by_origin)
    for r in records: origins.update(l.target for l in r.links)
    if len(origins)>10000: raise ValueError("LSDB 节点超过 10,000 个上限。")
    nodes, mappings = {}, {}
    for origin in sorted(origins):
        nid=identity("lsdb-node",batch.id,origin)
        is_pseudo=pseudo(origin)
        node=dict(id=nid,protocol_id=origin,kind="segment" if is_pseudo else "device",label=origin,
                  vendor="Unknown",mapping_status="lsdb_only",device_id=None,instance_id=None,max_paths=None,forwarding_mode='unknown',metric_path_limit=None,
                  source_refs=[ref.model_dump() for r in by_origin.get(origin,[]) for ref in r.source_refs])
        if origin not in by_origin: diag("missing_record",f"{origin} 被引用但没有有效详细记录。")
        candidates=[]
        if not is_pseudo and request.mode=="fused":
            eligible=[i for i in instances.values() if contexts.get(i.routing_context_id,{}).get("local_name")==batch.context_name
                and (batch.scope in i.areas if batch.protocol=="ospf" else batch.scope in i.levels)]
            candidates=[i for i in eligible if i.router_id and match_id(i.router_id)==match_id(router_id(origin))]
            if not candidates and batch.protocol=="isis":
                # Aliases alone are labels. Correlate only with explicit IPv4
                # addresses advertised in this LSP and configured on that instance.
                observed_addresses={a for r in by_origin.get(origin,[]) for a in r.addresses}
                if observed_addresses:
                    candidates=[i for i in eligible if i.router_id and any(
                        address.get("address") in observed_addresses for iface in interfaces.values()
                        if iface["device_id"]==i.device_id and iface.get("routing_context_id")==i.routing_context_id
                        for address in iface.get("addresses",[]) if address.get("family")==4)]
            explicit=request.mappings.get(origin)
            if explicit:
                i=instances.get(explicit)
                if not i or not (batch.scope in i.areas if batch.protocol=="ospf" else batch.scope in i.levels):
                    raise ValueError("手动关联必须引用所选协议与范围内的配置实例。")
                candidates=[i]
            if len(candidates)==1:
                i=candidates[0]; mappings[origin]=i
                node.update(label=devices[i.device_id]["hostname"],vendor=devices[i.device_id].get("vendor","Unknown"),
                            device_id=i.device_id,instance_id=i.id,max_paths=i.max_paths,forwarding_mode=i.forwarding_mode,
                            metric_path_limit=None,mapping_status="matched")
            elif len(candidates)>1:
                node.update(mapping_status="ambiguous")
                diag("ambiguous_identity",f"{origin} 对应多个配置实例，请手动关联；观测 SPF 仍独立于配置。",False)
        nodes[origin]=node
    if set(request.mappings)-origins: raise ValueError("手动关联包含此 LSDB 中不存在的节点。")
    if len({i.id for i in mappings.values()}) != len(mappings): raise ValueError("同一配置实例不能关联到两个不同 LSDB 节点。")
    raw=[]
    for origin,rs in by_origin.items():
        links=[l for r in rs for l in r.links]
        if batch.protocol=="isis":
            wide_targets={l.target for l in links if l.encoding=="wide"}
            # Both TLV 2 and TLV 22 may describe the same link. Prefer extended
            # encoding for that neighbor; never turn dual encoding into ECMP.
            links=[l for l in links if l.encoding!="narrow" or l.target not in wide_targets]
        seen=set()
        for l in links:
            refs=[r.model_dump() for r in l.source_refs]
            key=(l.target,l.local_ip,l.remote_ip,l.local_id,l.remote_id)
            if key in seen:
                diag("parallel_identity_unknown",f"{origin} → {l.target} 有重复邻接但缺少唯一链路标识；未猜测并行链路数量。",True,refs);continue
            seen.add(key)
            if batch.protocol=="ospf" and not pseudo(origin) and l.cost==0:
                diag("invalid_transit_metric", "OSPF 路由器间链路开销必须大于零。",True,refs);continue
            if l.target==origin:
                diag("self_link",f"{origin} 出现自环邻接。",True,refs);continue
            if l.cost==16777215 and batch.protocol=="isis":
                diag("excluded_metric",f"{origin} → {l.target} 使用最大链路度量，未用于标准 SPF。",False,refs)
            raw.append(dict(id=identity("lsdb-edge",batch.id,origin,l.target,str(key)),source=nodes[origin]["id"],target=nodes[l.target]["id"],
                origin=origin,peer=l.target,cost=l.cost,local_ip=l.local_ip,remote_ip=l.remote_ip,local_id=l.local_id,
                remote_id=l.remote_id,source_refs=refs))
    if len(raw)>100000: raise ValueError("LSDB 链路超过 100,000 条上限。")
    pairs={(a["origin"],a["peer"]) for a in raw}
    valid=[]
    for a in raw:
        if (a["peer"],a["origin"]) not in pairs:
            diag("one_way_adjacency",f"{a['origin']} → {a['peer']} 缺少反向声明，未用于 SPF。",True,a["source_refs"])
        elif not (batch.protocol=="isis" and a["cost"]==16777215): valid.append(a)
    outgoing=defaultdict(list)
    for a in valid: outgoing[a["origin"]].append(a)
    arcs=[]
    for a in valid:
        if pseudo(a["origin"]): continue
        paths=[(a,None)] if not pseudo(a["peer"]) else [(a,b) for b in outgoing[a["peer"]] if not pseudo(b["peer"]) and b["peer"]!=a["origin"]]
        for first,second in paths:
            peer=second["peer"] if second else first["peer"]
            if second and second["cost"]!=0:
                diag("pseudonode_metric", "伪节点到设备的度量应为 0。",True,second["source_refs"]);continue
            back=[b for b in outgoing[peer] if b["peer"]==(a["peer"] if second else a["origin"])]
            next_hop=first["remote_ip"] or (back[0]["local_ip"] if len(back)==1 else None)
            arcs.append(dict(id=identity("forward",first["id"],second["id"] if second else ""),source=first["source"],target=nodes[peer]["id"],
                source_device_id=first["source"],target_device_id=nodes[peer]["id"],origin=a["origin"],peer=peer,
                cost=first["cost"],link_ids=[first["id"]]+([second["id"]] if second else []),local_ip=first["local_ip"],
                next_hop=next_hop,interface_id="",peer_interface_id="",binding_id="",config_cost=None,status="lsdb_only",
                source_refs=first["source_refs"]+ (second["source_refs"] if second else []),config_source_refs=[]))
            if len(arcs)>100000: raise ValueError("广播域展开后超过 100,000 条转发邻接上限。")
    # Configuration candidate adjacencies provide port evidence; never add them to observed SPF.
    binding_by_iface=defaultdict(list)
    for b in facts.bindings:
        if b.instance_id in instances and b.scope==batch.scope and not b.passive and b.network_type!="loopback":
            binding_by_iface[b.interface_id].append(b)
    cfg_pairs=[]; segments=defaultdict(list)
    for l in snapshot.get("links",[]):
        if l.get("review_state")=="rejected" or 4 not in l.get("families",[]): continue
        if l.get("relation_type")=="segment_member": segments[l["device_b_id"]].append(l["interface_a_id"])
        elif l.get("interface_b_id"): cfg_pairs.append((l["interface_a_id"],l["interface_b_id"]))
    for members in segments.values():
        if len(members)<=128: cfg_pairs.extend(combinations(members,2))
    candidates=defaultdict(list)
    for a,b in cfg_pairs:
        for x,y in ((a,b),(b,a)):
            for left in binding_by_iface[x]:
                for right in binding_by_iface[y]:
                    candidates[(left.instance_id,right.instance_id)].append((left,right))
    # Keep configured devices absent from this observation visible in fusion mode.
    if request.mode=="fused":
        mapped_ids={i.id for i in mappings.values()}
        for i in instances.values():
            if i.id in mapped_ids or contexts.get(i.routing_context_id,{}).get("local_name")!=batch.context_name:
                continue
            if not (batch.scope in i.areas if batch.protocol=="ospf" else batch.scope in i.levels):
                continue
            if i.router_id:
                rid=i.router_id.lower()
                origin=rid if batch.protocol=="ospf" else ".".join(rid.replace(".","")[j:j+4] for j in (0,4,8))+".00"
            else: origin="config:"+i.id
            if origin in nodes:
                continue
            nodes[origin]=dict(id=identity("lsdb-node",batch.id,origin),protocol_id=origin,kind="device",
                label=devices[i.device_id]["hostname"],vendor=devices[i.device_id].get("vendor","Unknown"),
                mapping_status="config_only",observed=False,device_id=i.device_id,instance_id=i.id,max_paths=i.max_paths,source_refs=[])
            mappings[origin]=i
    used=set(); comparisons=[]
    if request.mode=="fused":
        for a in arcs:
            src,dst=mappings.get(a["origin"]),mappings.get(a["peer"])
            if not src or not dst: continue
            options=candidates.get((src.id,dst.id),[])
            if a["local_ip"]:
                options=[(x,y) for x,y in options if any(addr.get("address")==a["local_ip"] for addr in interfaces[x.interface_id].get("addresses",[]))]
            if a["next_hop"]:
                options=[(x,y) for x,y in options if any(addr.get("address")==a["next_hop"] for addr in interfaces[y.interface_id].get("addresses",[]))]
            if len(options)==1:
                x,y=options[0];used.add((x.id,y.id))
                iface=interfaces[x.interface_id]
                status="consistent" if x.cost==a["cost"] else "cost_difference" if x.cost is not None else "config_cost_unknown"
                if iface.get("admin_state")=="disabled" or iface.get("effective_state")!="active": status="state_difference"
                a.update(interface_id=x.interface_id,peer_interface_id=y.interface_id,binding_id=x.id,config_cost=x.cost,status=status,
                    interface_name=iface["raw_name"],config_source_refs=[r.model_dump() for r in x.source_refs])
            elif len(options)>1: a["status"]="port_ambiguous"
            comparisons.append({**a,"observed_cost":a["cost"]})
        reverse_mapping={i.id:origin for origin,i in mappings.items()}
        for (src,dst),options in candidates.items():
            if src not in reverse_mapping or dst not in reverse_mapping: continue
            for x,y in options:
                if (x.id,y.id) not in used:
                    comparisons.append(dict(id=identity("config-only",x.id,y.id),source=nodes[reverse_mapping[src]]["id"],target=nodes[reverse_mapping[dst]]["id"],
                        status="config_only",config_cost=x.cost,observed_cost=None,interface_id=x.interface_id,interface_name=interfaces[x.interface_id]["raw_name"],
                        source_refs=[],config_source_refs=[r.model_dump() for r in x.source_refs]))
    destinations=[]
    for origin,rs in by_origin.items():
        if pseudo(origin): continue
        prefixes={}
        for r in rs:
            for p in r.prefixes:
                if p.prefix not in prefixes or p.cost<prefixes[p.prefix].cost: prefixes[p.prefix]=p
        for p in prefixes.values():
            destinations.append(dict(id=identity("prefix",batch.id,origin,p.prefix),node_id=nodes[origin]["id"],origin=origin,
                prefix=p.prefix,cost=p.cost,source_refs=[r.model_dump() for r in p.source_refs]))
    edge_states=defaultdict(set)
    for arc in arcs:
        for lid in arc["link_ids"]: edge_states[lid].add(arc["status"])
    edges=[]
    for a in raw:
        states=edge_states[a["id"]]
        state=next(iter(states)) if len(states)==1 else "observed"
        edges.append(dict(id=a["id"],link_id=a["id"],source=a["source"],target=a["target"],label=str(a["cost"]),
            device_a_id=a["source"],device_b_id=a["target"],interface_a_id="",interface_b_id="",relation_type="lsdb",origin="observed",
            review_state="confirmed",families=[4],prefixes=[],segment_ids=[],evidence=[],diagnostics=[],status=state,source_refs=a["source_refs"],cost=a["cost"]))
    for c in comparisons:
        if c["status"]=="config_only":
            edges.append(dict(id=c["id"],source=c["source"],target=c["target"],label=f"配置 {c['config_cost'] if c['config_cost'] is not None else '?'}",device_a_id=c["source"],device_b_id=c["target"],
                interface_a_id=c["interface_id"],interface_b_id="",relation_type="lsdb",origin="configuration",review_state="pending",families=[4],prefixes=[],segment_ids=[],evidence=[],diagnostics=[],status="config_only"))
    node_by_id={n["id"]:n for n in nodes.values()}
    result=dict(batch_id=batch.id,config_snapshot_id=snapshot.get("id"),import_config_snapshot_id=batch.config_snapshot_id,
        mode=request.mode,topology=dict(nodes=list(nodes.values()),edges=edges),nodes=list(nodes.values()),destinations=destinations,
        comparisons=comparisons,diagnostics=diagnostics,arcs=arcs,simulation=None,config_simulation=None)
    if request.source is not None or request.destination is not None:
        src=node_by_id.get(request.source)
        dest=next((p for p in destinations if p["id"]==request.destination),None)
        if not src or src["kind"]!="device" or src.get("observed") is False or not dest: raise ValueError("源设备或目的前缀不属于当前批次。")
        if any(d["prefix"]==dest["prefix"] and d["node_id"]!=dest["node_id"] for d in destinations):
            diag("multiple_prefix_origins", "所选前缀由多个节点发布；当前不能将指定发布者当作唯一转发目的。")
        assumptions=["按所选采集批次的 LSDB 还原，采集时间不代表当前网络状态。", "仅限单 Area / Level 的 Standard IPv4 内部前缀；未模拟跨域选路或实际 FIB Hash。", "按每台设备的等价下一跳均匀分担，伪节点不参与分担。", "未关联到配置的节点仍可计算设备路径；物理端口、下一跳 IP 或 ECMP 上限可能未知。"]
        sim=dict(status="incomplete",total_cost=None,path_count="0",paths=[],hops=[],forwarding=[],paths_truncated=False,forwarding_status="theoretical",diagnostics=deepcopy(diagnostics))
        if not any(d.get("blocking",True) for d in diagnostics):
            excluded={nodes[o]["id"] for o,rs in by_origin.items() if batch.protocol=="isis" and not pseudo(o) and (any(r.overload for r in rs) or not any(r.ipv4 for r in rs))}
            eligible=[a for a in arcs if a["source"]==src["id"] or a["source"] not in excluded]
            sim=calculate(eligible,src["id"],dest["node_id"],terminal_cost=dest["cost"],path_limit=request.path_limit,
                nodes={n["id"]:{**n,"device_id":n["id"]} for n in nodes.values()})
            sim["diagnostics"]=diagnostics+sim["diagnostics"]
        sim.update(source_device_id=src["id"],destination_device_id=dest["node_id"],assumptions=assumptions)
        result["simulation"]=sim
        if request.mode=="fused" and src["instance_id"] and nodes[dest["origin"]]["instance_id"]:
            target_inst=nodes[dest["origin"]]["instance_id"]
            bids={b.id for b in facts.bindings if b.instance_id==target_inst and b.scope==batch.scope}
            ads=[a for a in facts.advertisements if a.binding_id in bids and a.prefix==dest["prefix"] and interfaces.get(a.interface_id,{}).get("kind")=="loopback" and a.prefix.endswith("/32")]
            if len(ads)==1:
                try:
                    result["config_simulation"]=simulate(snapshot,SimulationRequest(version=request.version,protocol=batch.protocol,scope=batch.scope,source_instance_id=src["instance_id"],destination_advertisement_id=ads[0].id,path_limit=request.path_limit))
                except ValueError:
                    result["config_simulation"]=dict(status="incomplete",total_cost=None,path_count="0",paths=[],hops=[],
                        diagnostics=[dict(code="config_not_comparable",message="当前配置缺少可比较的源接口或目的前缀，观测结果不受影响。")])
    return result
