"""血缘模型：节点（Node）+ 边（Edge）。

节点类型：
- ``point``     测点（物理资产）
- ``dataset``   数据集/表
- ``report``    报表/看板
- ``task``      计算任务/脚本
- ``api``       对外接口

边方向：上游 -> 下游。改名影响面 = 从 point 节点出发沿边向下游 BFS。

约束：
- ``(node_type, node_key)`` 唯一，保证同一实体只有一个节点
- 插入边时做环检测（见 ``src/g05/lineage/impact.py``，B2 负责），防循环依赖
- 数据库层另有触发器 ``trg_edge_no_self_loop`` 兜底禁止自环（见 ``core/schema.py``）

外键策略：``upstream_id`` / ``downstream_id`` 均 CASCADE —— 节点删除时边自动清理，
边是从属关系，没有独立存在的意义。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from g05.models.base import Base, TimestampMixin


class LineageNode(Base, TimestampMixin):
    """血缘节点：一个被血缘关系引用的实体。"""

    __tablename__ = "lineage_node"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 业务键：测点用 point_code，报表用 report_id
    node_key: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 归属系统/负责人，影响面通知用
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("node_type", "node_key", name="uq_lineage_node_type_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<LineageNode {self.node_type}:{self.node_key}>"

    __dto_fields__ = (
        "id",
        "node_type",
        "node_key",
        "node_name",
        "owner",
        "created_at",
        "updated_at",
    )


class LineageEdge(Base, TimestampMixin):
    """血缘边：上游 -> 下游的一条依赖。"""

    __tablename__ = "lineage_edge"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    upstream_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lineage_node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    downstream_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lineage_node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 字段级血缘（加分项）：哪一列被引用
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 引用方式：read / join / aggregate
    relation: Mapped[str] = mapped_column(
        String(16), nullable=False, default="read", server_default="read"
    )

    __table_args__ = (
        UniqueConstraint("upstream_id", "downstream_id", "field_name", name="uq_lineage_edge"),
        Index("ix_edge_downstream", "downstream_id"),
    )

    __dto_fields__ = (
        "id",
        "upstream_id",
        "downstream_id",
        "field_name",
        "relation",
        "created_at",
        "updated_at",
    )
