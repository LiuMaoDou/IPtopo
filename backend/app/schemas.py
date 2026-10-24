from __future__ import annotations
import math
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("项目名称不能为空")
        return value.strip()

class VersionRequest(BaseModel):
    version: int = Field(ge=1)

class DomainRequest(VersionRequest):
    domains: dict[str, Any]

class OverrideRequest(VersionRequest):
    operation: Literal["confirm_link", "suppress_link", "force_link", "rebind_endpoint", "assign_domain", "split_segment", "override_field", "add_device", "add_interface", "add_note"]
    target: dict[str, Any] = Field(default_factory=dict)
    value: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)
    baseline: dict[str, Any] | None = None

class OverrideStatus(VersionRequest):
    status: Literal["active", "undone"]

class View(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    positions: dict[str, dict[str, float]] = Field(default_factory=dict)
    pinned: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    groups: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, Any] = Field(default_factory=dict)
    layout: str = "preset"
    zoom: float = Field(default=1, gt=0, le=100)
    pan: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})

    @field_validator("positions")
    @classmethod
    def valid_positions(cls, value):
        if len(value) > 100_000:
            raise ValueError("节点数过多")
        for point in value.values():
            if set(point) != {"x", "y"} or any(not math.isfinite(v) or abs(v) > 1e9 for v in point.values()):
                raise ValueError("节点坐标无效")
        return value

    @field_validator("pan")
    @classmethod
    def valid_pan(cls, value):
        if set(value) != {"x", "y"} or any(not math.isfinite(v) for v in value.values()):
            raise ValueError("视口无效")
        return value

class ViewRequest(VersionRequest):
    view: View
