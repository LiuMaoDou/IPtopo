"""Separate unknown extensions from unresolved basic IPv4 graph facts.

Only command keywords are inspected. The original statement may contain an
authentication secret and must never be included in a diagnostic.
"""
import re


def classify_statement(statement, code, message):
    words = statement.lower().split() if isinstance(statement, str) else [str(w).lower() for w in statement]
    while words and words[0] in {"no", "undo", "ip", "ospf", "isis"}:
        words = words[1:]
    if words[:1] == ["area"] and len(words) > 2:
        words = words[2:]
    if words[:1] == ["level"]:
        if len(words) > 1 and words[1] not in {"1", "2"}:
            return "invalid_protocol_level", "协议 Level 无效，不能确定基础 IPv4 路由范围。"
        words = words[2:]
    if not words:
        return code, message
    keyword = words[0]
    if ("authentication" in keyword or keyword in {"key-chain", "keychain", "message-digest-key",
            "hello-password", "lsp-password", "area-password", "domain-password"}):
        return "unmodeled_authentication", "认证语义未建模；本次基础图推演假设认证与邻接正常。"
    if keyword in {"metric-style", "cost-style", "wide-metrics-only"}:
        return "unknown_metric_encoding", "显式度量编码无法确定，不能套用默认编码。"
    if keyword in {"network-type", "interface-type"} or keyword == "network" and len(words) > 1 and not re.fullmatch(r"[\d.]+", words[1]):
        return "unsupported_network_type", "显式接口网络类型无法确定，不能套用默认网络类型。"
    if keyword == "circuit-type" and len(words) > 1 and words[1] not in {"level-1", "level-2-only", "level-1-2"}:
        return "unsupported_network_type", "显式 IS-IS 接口网络类型无法确定。"
    if keyword in {"cost", "metric", "circuit-cost", "reference-bandwidth", "auto-cost"}:
        return "invalid_cost", "显式开销配置无法确定，不能套用默认开销。"
    if keyword in {"net", "network-entity"}:
        return "invalid_net", "显式 IS-IS NET 无法解析。"
    if keyword in {"enable", "disable", "shutdown", "admin-state", "passive", "passive-interface", "silent", "silent-interface"}:
        return "unresolved_protocol_state", "显式协议启停或被动接口状态无法确定，不能假设协议处于活动状态。"
    if keyword in {"is-level", "is-type", "circuit-level", "level-capability"}:
        return "invalid_protocol_level", "显式协议 Level 配置无法确定。"
    if keyword in {"hello-interval", "dead-interval"} or keyword in {"timer", "timers"} and len(words) > 1 and words[1] in {"hello", "dead"}:
        return "invalid_adjacency_timers", "显式 Hello / Dead 定时器无法解析，不能假设两端一致。"
    if keyword in {"network", "router-id", "system-id", "area-id", "area-address"}:
        return "unresolved_protocol_identity", "显式协议标识或接口选择范围无法解析。"
    return code, message
