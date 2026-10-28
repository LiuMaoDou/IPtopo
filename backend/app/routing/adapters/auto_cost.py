"""Huawei automatic-cost bandwidth model, including explicitly labeled estimates.

Nominal port speeds, all configured LAG members and parent bandwidth inheritance
are the user's confirmed offline simulation policy, not observed link state.
"""
from fractions import Fraction
import re


def bandwidth_resolver(interfaces, blocks):
    by_id = {i["id"]: i for i in interfaces}
    by_name = {i["raw_name"].lower(): i for i in interfaces}
    cache = {}

    def resolve(iface, visiting=frozenset()):
        iid = iface["id"]
        if iid in visiting:
            return None, []
        if iid in cache:
            return cache[iid]
        visiting = visiting | {iid}
        name = iface["raw_name"]
        if iface.get("assumed_bandwidth_mbps"):
            answer = (Fraction(str(iface["assumed_bandwidth_mbps"])),
                      [f"{name} 按人工确认带宽 {iface['assumed_bandwidth_mbps']:g} Mbit/s 推演，不代表实测速率。"])
            cache[iid] = answer
            return answer
        if iface.get("configured_bandwidth_mbps"):
            answer = (Fraction(str(iface["configured_bandwidth_mbps"])), [])
            cache[iid] = answer
            return answer
        for _, line in blocks.get(name.lower(), (0, []))[1]:
            match = re.fullmatch(r"bandwidth\s+(\d+)(?:\s+(kbps|mbps|gbps))?", line, re.I)
            speed = re.fullmatch(r"speed\s+(\d+)", line, re.I)
            if match and int(match[1]) > 0:
                scale = {"kbps": Fraction(1, 1000), "mbps": 1, "gbps": 1000}[match[2].lower() if match[2] else "mbps"]
                answer = (Fraction(int(match[1])) * scale, [])
                iface["configured_bandwidth_mbps"] = float(answer[0])
                break
            if speed and int(speed[1]) > 0:
                answer = (Fraction(int(speed[1])), [])
                iface["configured_bandwidth_mbps"] = float(answer[0])
                break
        else:
            parent = by_id.get(iface.get("parent_interface_id"))
            if parent is None and "." in name:
                parent = by_name.get(name.rsplit(".", 1)[0].lower())
            if parent:
                value, notes = resolve(parent, visiting)
                answer = (value, notes + ([f"{name} 的带宽按父接口 {parent['raw_name']} 继承。"] if value else []))
            elif iface.get("kind") == "aggregate" or name.lower().startswith("eth-trunk"):
                members = [by_id.get(mid) or by_name.get(mid.lower()) for mid in dict.fromkeys(iface.get("aggregate_members", []))]
                resolved = [resolve(member, visiting) if member else (None, []) for member in members]
                if resolved and all(value for value, _ in resolved):
                    value = sum((value for value, _ in resolved), Fraction())
                    answer = (value, [note for _, notes in resolved for note in notes] +
                              [f"{name} 按全部 {len(members)} 个配置成员带宽之和 {float(value):g} Mbit/s 推演，不代表当前活动成员。"])
                else:
                    answer = (None, [])
            else:
                match = re.match(r"(?:(\d+)ge|gigabitethernet|xgigabitethernet|tengigabitethernet|ge|fastethernet)(?=\d)", name, re.I)
                if not match:
                    answer = (None, [])
                else:
                    prefix = match[0].lower()
                    value = Fraction(int(match[1]) * 1000 if match[1] else 100 if prefix == "fastethernet"
                                     else 10000 if prefix in ("xgigabitethernet", "tengigabitethernet") else 1000)
                    answer = (value, [f"{name} 按接口类型的标称带宽 {float(value):g} Mbit/s 推演，不代表实际协商速率。"])
        cache[iid] = answer
        return answer

    return resolve


def automatic_cost(bandwidth, reference, style, compatible=False):
    if not bandwidth:
        return None
    if style in ("wide", "wide-compatible"):
        if not reference:
            return None
        # Routing metrics are integers. Preserve the conversion as a visible
        # simulation assumption rather than presenting it as an observed cost.
        return max(1, int(Fraction(reference, 1) / bandwidth * (1 if compatible else 10)))
    return next((cost for limit, cost in ((10, 60), (100, 50), (155, 40), (622, 30), (2500, 20)) if bandwidth <= limit), 10)
