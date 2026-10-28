"""Versioned routing contract. No vendor parsers or UI dependencies."""
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Exact legacy codes, not an "unsupported_*" prefix rule: missing base facts,
# invalid metrics, unknown network/metric encodings and truncated input remain
# blocking. Runtime classification also applies to already saved snapshots.
IGNORED_SEMANTIC_CODES = frozenset({
    "unsupported_protocol_command", "unsupported_protocol_statement",
    "unsupported_interface_command", "unsupported_interface_statement",
    "unsupported_area_command", "unsupported_area_statement",
    "unknown_protocol_command", "unknown_protocol_statement",
    "unknown_interface_command", "unknown_interface_statement",
    "unknown_area_command", "unknown_area_statement",
    "unmodeled_authentication",
})


def identity(*parts: str) -> str:
    return "igp_" + sha256("\0".join(parts).encode()).hexdigest()[:24]


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(Fact):
    source_file_id: str
    line: int = Field(ge=1)


class Issue(Fact):
    code: str
    message: str
    device_id: str
    instance_id: str | None = None
    interface_id: str | None = None
    blocking: bool = True
    protocol: Literal["ospf", "isis"] | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)


class Instance(Fact):
    id: str
    device_id: str
    routing_context_id: str
    protocol: Literal["ospf", "isis"]
    local_name: str
    router_id: str | None = None
    areas: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    max_paths: int | None = Field(default=None, ge=1, le=256)
    metric_style: Literal["standard", "narrow", "wide", "both"] = "standard"
    level_metric_styles: dict[Literal["1", "2"], Literal["narrow", "wide", "both"]] = Field(default_factory=dict)
    defaults_profile: str | None = None
    forwarding_mode: Literal["unknown", "single_next_hop", "ecmp"] = "unknown"
    metric_path_limit: int | None = Field(default=None,ge=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class Binding(Fact):
    id: str
    device_id: str
    instance_id: str
    interface_id: str
    scope: str  # OSPF area ID, or IS-IS level "1"/"2".
    passive: bool = False
    network_type: Literal["p2p", "broadcast", "loopback", "unsupported"]
    cost: int | None = Field(default=None, ge=0, le=16777214)
    cost_origin: Literal["explicit", "profile_default", "unknown"] = "unknown"
    metric_limit: int = Field(default=16777214, ge=1, le=16777214)
    hello: int | None = None
    dead: int | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)


class Advertisement(Fact):
    id: str
    device_id: str
    binding_id: str
    interface_id: str
    address_id: str
    address: str
    prefix: str
    source_refs: list[SourceRef] = Field(default_factory=list)


class RoutingFacts(Fact):
    schema_version: Literal[1] = 1
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    adapter_devices: list[str] = Field(default_factory=list)
    instances: list[Instance] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)
    advertisements: list[Advertisement] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        for rows in (self.instances, self.bindings, self.advertisements):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError("路由事实 ID 重复。")
        instances = {i.id: i for i in self.instances}
        bindings = {b.id: b for b in self.bindings}
        for b in self.bindings:
            if b.instance_id not in instances or b.device_id != instances[b.instance_id].device_id:
                raise ValueError("协议接口实例引用无效。")
        for a in self.advertisements:
            b = bindings.get(a.binding_id)
            if not b or a.device_id != b.device_id or a.interface_id != b.interface_id:
                raise ValueError("前缀发布引用无效。")
        return self


class Scenario(Fact):
    disabled_interfaces: list[str] = Field(default_factory=list, max_length=10000)
    metric_overrides: dict[str, Annotated[int, Field(strict=True, ge=1, le=16777214)]] = Field(default_factory=dict)
    unknown_cost: int | None = Field(default=None, ge=1, le=65535)


class SimulationRequest(Fact):
    version: int = Field(ge=1)
    protocol: Literal["ospf", "isis"]
    source_instance_id: str
    destination_advertisement_id: str
    scope: str = Field(min_length=1, max_length=64)
    scenario: Scenario = Field(default_factory=Scenario)
    path_limit: int = Field(default=64, ge=1, le=256)


def merge_facts(current: dict, previous: dict | None = None, keep_devices: set | None = None) -> dict:
    """Merge immutable per-device facts; never retain facts for replaced devices."""
    result = RoutingFacts.model_validate(current).model_dump()
    old = RoutingFacts.model_validate(previous or {}).model_dump()
    for key in ("instances", "bindings", "advertisements", "issues"):
        result[key] = [x for x in old[key] if x["device_id"] in (keep_devices or set())] + result[key]
    result["adapter_versions"] = {**old["adapter_versions"], **result["adapter_versions"]}
    result["adapter_devices"] = sorted(set(result["adapter_devices"]) | (set(old["adapter_devices"]) & (keep_devices or set())))
    return result


def validate_snapshot_routing(snapshot: dict) -> RoutingFacts:
    facts = RoutingFacts.model_validate(snapshot.get("routing", {}))
    devices = {d["id"] for d in snapshot.get("devices", [])}
    interfaces = {i["id"]: i for i in snapshot.get("interfaces", [])}
    contexts = {c["id"]: c for c in snapshot.get("contexts", [])}
    for instance in facts.instances:
        if instance.device_id not in devices or contexts.get(instance.routing_context_id, {}).get("device_id") != instance.device_id:
            raise ValueError("路由事实中的设备 / 路由上下文引用无效。")
    for binding in facts.bindings:
        if interfaces.get(binding.interface_id, {}).get("device_id") != binding.device_id:
            raise ValueError("路由事实中的接口引用无效。")
    return facts
