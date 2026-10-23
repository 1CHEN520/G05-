"""API 数据传输对象（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int

    @property
    def pages(self) -> int:
        return (self.total + self.size - 1) // self.size if self.size else 0


# ---------------- Device ----------------
class DeviceBase(BaseModel):
    device_code: str = Field(..., max_length=32, description="设备编号，业务唯一键")
    device_name: str = Field(..., max_length=64)
    device_type: str = Field(..., max_length=32)
    location: str = Field(..., max_length=128)
    status: str = "运行"


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    device_name: str | None = None
    device_type: str | None = None
    location: str | None = None
    status: str | None = None


class DeviceOut(ORMModel, DeviceBase):
    id: int
    version: int
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# ---------------- MeasurePoint ----------------
class PointBase(BaseModel):
    device_id: int
    point_code: str = Field(..., max_length=48)
    point_name: str = Field(..., max_length=64)
    point_type: str = Field(..., max_length=32)
    unit: str = Field(..., max_length=16)


class PointCreate(PointBase):
    pass


class PointOut(ORMModel, PointBase):
    id: int
    # 由 MeasurePoint 的只读属性提供，前端不必再查设备表
    device_code: str | None = None
    device_name: str | None = None
    version: int
    is_deleted: bool
    superseded_by: int | None = None
    renamed_from: str | None = None
    change_note: str | None = None
    created_at: datetime
    updated_at: datetime


class PointRename(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=64, description="新测点名称")
    operator: str = Field("system", max_length=64)
    reason: str | None = Field(None, description="变更原因，写入审计")


class RenameResult(BaseModel):
    point_id: int
    old_name: str
    new_name: str
    new_version: int
    superseded_by: int
    affected: dict[str, Any] = Field(default_factory=dict)


# ---------------- Lineage ----------------
class LineageNodeOut(BaseModel):
    id: int
    node_type: str
    node_key: str
    node_name: str
    owner: str | None = None
    depth: int = 0
    path: list[str] = Field(default_factory=list)


class ImpactOut(BaseModel):
    root: LineageNodeOut | None
    direction: str
    total: int
    truncated: bool
    cycles: list[list[str]] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    nodes: list[LineageNodeOut] = Field(default_factory=list)


# ---------------- Audit ----------------
class AuditOut(ORMModel):
    id: int
    table_name: str
    record_id: int | None
    business_key: str | None
    action: str
    operator: str
    old_value: str | None
    new_value: str | None
    reason: str | None
    changed_at: datetime


# ---------------- Import ----------------
class ImportSummary(BaseModel):
    ok: bool
    file: str
    inserted: dict[str, int]
    updated: dict[str, int]
    elapsed_ms: float
    message: str
    rolled_back: bool
    reports: list[dict] = Field(default_factory=list)


# ---------------- Common ----------------
class HealthOut(BaseModel):
    ok: bool
    database: dict
    app: dict
