"""Neutral, versioned observation contract; no CLI syntax or UI dependencies."""
from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator, model_validator
from ..routing.model import Fact, SourceRef


class Diagnostic(Fact):
    code: str
    message: str
    blocking: bool = True
    source_refs: list[SourceRef] = Field(default_factory=list)


class LinkStateLink(Fact):
    target: str
    cost: int = Field(ge=0, le=16777215)
    kind: Literal["p2p", "transit", "member"] = "p2p"
    local_ip: str | None = None
    remote_ip: str | None = None
    local_id: str | None = None
    remote_id: str | None = None
    encoding: Literal["standard", "narrow", "wide"] = "standard"
    source_refs: list[SourceRef] = Field(default_factory=list)


class Prefix(Fact):
    prefix: str
    cost: int = Field(ge=0, le=4294967295)
    source_refs: list[SourceRef] = Field(default_factory=list)

    @field_validator("prefix")
    @classmethod
    def valid_prefix(cls, value):
        import ipaddress
        n = ipaddress.IPv4Network(value, strict=False)
        return str(n)


class Record(Fact):
    key: str
    kind: Literal["router", "network", "lsp", "other"]
    origin: str
    sequence: int = Field(ge=0, le=4294967295)
    checksum: int = Field(ge=0, le=65535)
    age: int = Field(ge=0, le=65535)
    expired: bool = False
    detailed: bool = True
    fragment: int = Field(default=0, ge=0, le=255)
    overload: bool = False
    ipv4: bool = True
    hostname: str | None = None
    addresses: list[str] = Field(default_factory=list, max_length=10000)
    links: list[LinkStateLink] = Field(default_factory=list, max_length=10000)
    prefixes: list[Prefix] = Field(default_factory=list, max_length=10000)
    source_refs: list[SourceRef] = Field(default_factory=list)


class Batch(Fact):
    schema_version: Literal[1] = 1
    id: str
    vendor: Literal["huawei", "cisco", "juniper", "nokia"] = "huawei"
    adapter: Literal["huawei-vrp-lsdb/1", "cisco-lsdb/1", "juniper-lsdb/1", "nokia-lsdb/1"] = "huawei-vrp-lsdb/1"
    aliases: dict[str, str] = Field(default_factory=dict, max_length=10000)
    name: str = Field(min_length=1, max_length=200)
    protocol: Literal["ospf", "isis"]
    process_id: str = Field(min_length=1, max_length=64)
    scope: str = Field(min_length=1, max_length=64)
    context_name: str = Field(default="default", max_length=128)
    domain: str = Field(default="default", min_length=1, max_length=128)
    collector: str = Field(default="", max_length=200)
    collected_at: datetime | None = None
    imported_at: datetime
    declared_complete: bool = False
    config_snapshot_id: str | None = None
    records: list[Record] = Field(default_factory=list, max_length=20000)
    diagnostics: list[Diagnostic] = Field(default_factory=list, max_length=20000)
    source_files: list[dict[str, str]] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def references(self):
        expected='huawei-vrp-lsdb/1' if self.vendor=='huawei' else self.vendor+'-lsdb/1'
        if self.adapter!=expected:
            raise ValueError('LSDB 厂商与适配器版本不一致。')
        ids = [s["id"] for s in self.source_files]
        if len(ids) != len(set(ids)):
            raise ValueError("LSDB 来源 ID 重复。")
        for record in self.records:
            for ref in [*record.source_refs, *[r for l in record.links for r in l.source_refs], *[r for p in record.prefixes for r in p.source_refs]]:
                if ref.source_file_id not in ids:
                    raise ValueError("LSDB 记录引用了不存在的来源。")
        return self


class AnalyzeRequest(Fact):
    version: int = Field(ge=1)
    mode: Literal["observed", "fused"] = "fused"
    mappings: dict[str, str] | None = Field(default=None, max_length=10000)
    source: str | None = None
    destination: str | None = None
    path_limit: int = Field(default=64, ge=1, le=256)
