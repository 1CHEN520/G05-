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
    """影响面查询响应。

    截断语义按 A 的裁定（2026-11-01，决定 1 方案 2）：
    ``total`` 是**完整可达节点数**（不含 root，截断与否都如实计数），
    ``returned`` 是本次实际返回的节点数；两者之差按类型记入
    ``omitted_by_type``，被谁截断记入 ``truncated_by``。

    按决定 3，``/impact`` **不含 edges** —— 边由 ``/graph`` 单独提供，
    双份传输只会给前端添乱。
    """

    root: LineageNodeOut | None
    direction: str
    total: int
    returned: int
    truncated: bool
    truncated_by: list[str] = Field(default_factory=list)
    omitted_by_type: dict[str, int] = Field(default_factory=dict)
    cycles: list[list[str]] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    nodes: list[LineageNodeOut] = Field(default_factory=list)


class GraphNodeOut(BaseModel):
    """血缘图节点（比 ``LineageNodeOut`` 少 depth/path，多用 type/key/name 简写）。"""

    id: int
    type: str
    key: str
    name: str
    owner: str | None = None


class GraphEdgeOut(BaseModel):
    """血缘图边。``source``/``target`` 为节点 id。"""

    source: int
    target: int
    field: str | None = None
    relation: str | None = None


class GraphOut(BaseModel):
    """血缘全图响应。

    按 A 的裁定（决定 2 方案 1）：保留 induced-graph 语义（边两端都在返回节点集内），
    加 ``total``/``returned``/``truncated`` 三个字段，让前端不再把前 N 个节点
    说成"全图"。**不引入 cursor 分页**；超过 1000 节点再启用，见 ADR-004"未来工作"。
    """

    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)
    total: int
    returned: int
    truncated: bool


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
