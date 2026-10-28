"""Vendor-independent SPF and per-router ideal ECMP on a forwarding graph.

Pseudonodes must be projected to real forwarding hops by the graph builder.
Zero-cost edges are supported when their shortest-path subgraph is acyclic.
"""
from collections import defaultdict, deque
from fractions import Fraction
from heapq import heappop, heappush


def calculate(arcs, source, target, *, terminal_cost=0, path_limit=64, nodes=None):
    nodes = nodes or {}
    result = dict(status="unreachable", total_cost=None, path_count="0", paths=[],
                  paths_truncated=False, hops=[], forwarding=[], diagnostics=[], forwarding_status="theoretical")
    reverse, outgoing = defaultdict(list), defaultdict(list)
    limited_nodes = set()
    for arc in arcs:
        if arc["cost"] < 0:
            raise ValueError("链路开销不能为负数。")
        reverse[arc["target"]].append(arc)
    distances = {target: terminal_cost if source != target else 0}
    heap = [(distances[target], target)]
    while heap:
        distance, node = heappop(heap)
        if distances[node] != distance:
            continue
        for arc in reverse[node]:
            candidate = distance + arc["cost"]
            limit = nodes.get(arc['source'],{}).get('metric_path_limit')
            if limit is not None and candidate > limit:
                limited_nodes.add(arc['source'])
                continue
            if candidate < distances.get(arc["source"], float("inf")):
                distances[arc["source"]] = candidate
                heappush(heap, (candidate, arc["source"]))
    if source not in distances:
        if limited_nodes:
            result['diagnostics'].append(dict(code='metric_path_limit',message='路径总开销超过节点支持的度量范围，未生成可用路径。'))
        result["diagnostics"].append(dict(code="no_path", message="所选协议范围内不可达。"))
        return result
    for arc in arcs:
        if arc["source"] != target and arc["source"] in distances and arc["target"] in distances:
            if distances[arc["source"]] == arc["cost"] + distances[arc["target"]]:
                outgoing[arc["source"]].append(arc)
    reachable, pending = {source}, deque([source])
    while pending:
        for a in outgoing[pending.popleft()]:
            if a["target"] not in reachable:
                reachable.add(a["target"]); pending.append(a["target"])
    degree = dict.fromkeys(reachable, 0)
    for n in reachable:
        outgoing[n].sort(key=lambda a: (nodes.get(a["target"], {}).get("label", a["target"]), a.get("interface_id") or "", a["id"]))
        for a in outgoing[n]:
            degree[a["target"]] += 1
    queue = deque(sorted(n for n, d in degree.items() if d == 0))
    order = []
    while queue:
        n = queue.popleft(); order.append(n)
        for a in outgoing[n]:
            degree[a["target"]] -= 1
            if not degree[a["target"]]:
                queue.append(a["target"])
    if len(order) != len(reachable):
        result.update(status="unsupported", diagnostics=[dict(code="zero_cost_cycle", message="最短路径子图含零开销环；无法确定无环 ECMP 下一跳，未输出分担比例。")])
        return result
    counts = {target: 1}
    for n in reversed(order):
        if n != target:
            counts[n] = sum(counts[a["target"]] for a in outgoing[n])
    traffic = defaultdict(Fraction, {source: Fraction(1)})
    for n in order:
        choices = outgoing[n]
        if not choices:
            continue
        meta = nodes.get(n, {})
        limit = meta.get("max_paths")
        if limit is not None and len(choices) > limit:
            result["forwarding_status"] = "limit_ambiguous"
            result["diagnostics"].append(dict(code="ecmp_limit", message=f"{meta.get('label', n)} 有 {len(choices)} 个等价下一跳，有效上限为 {limit}；保留理论路径，实际选中子集未知。", device_id=meta.get("device_id", n)))
        result["forwarding"].append(dict(device_id=meta.get("device_id", n), instance_id=n,
            next_hop_count=len(choices), configured_max_paths=limit, forwarding_mode=meta.get('forwarding_mode','unknown'), incoming_share_percent=round(float(traffic[n]*100), 6)))
        share = traffic[n] / len(choices)
        for a in choices:
            traffic[a["target"]] += share
            result["hops"].append({**a, "share_percent": round(float(share*100), 6), "local_share_percent": round(100/len(choices), 6)})
    path, shares = [], [Fraction(1)]
    stack = [(source, iter(outgoing[source]))]
    emitted = 0
    while stack and len(result["paths"]) < path_limit and emitted < 20000:
        n, choices = stack[-1]
        if n == target:
            result["paths"].append(dict(hops=[a["id"] for a in path],
                device_ids=[nodes.get(source, {}).get("device_id", source)] + [a["target_device_id"] for a in path],
                total_cost=distances[source], share_percent=round(float(shares[-1]*100), 6)))
            emitted += len(path)
        arc = next(choices, None) if n != target else None
        if arc is None:
            stack.pop()
            if path:
                path.pop(); shares.pop()
        else:
            path.append(arc); shares.append(shares[-1]/len(outgoing[n]))
            stack.append((arc["target"], iter(outgoing[arc["target"]])))
    result.update(status="ok", total_cost=distances[source], path_count=str(counts[source]), paths_truncated=len(result["paths"]) < counts[source])
    return result
