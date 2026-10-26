"""测点表（MeasurePoint）—— 资产目录的核心。

血缘设计：
- ``superseded_by`` 指向改名前的新测点记录，串起版本链
- ``renamed_from`` 保留历史名称，供审计与影响面回溯
- 改名不物理删除：旧行 is_deleted=1 + deleted_at，新行 version+1
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from g05.models.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from g05.models.alarm import Alarm
    from g05.models.device import Device
    from g05.models.record import Record


class MeasurePoint(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "measure_point"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # 外键必须建索引（否则级联删除与 join 全表扫）
    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # 测点编号：全局唯一，血缘节点标识
    point_code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True, index=True)
    point_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    point_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)

    # ---- 血缘/版本链 ----
    superseded_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("measure_point.id", ondelete="SET NULL"), nullable=True, index=True
    )
    renamed_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 变更原因说明，审计追溯时展示
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    device: Mapped[Device] = relationship(back_populates="points", lazy="joined")
    records: Mapped[list[Record]] = relationship(
        back_populates="point", cascade="all, delete-orphan", passive_deletes=True
    )
    alarms: Mapped[list[Alarm]] = relationship(
        back_populates="point", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_point_device_active", "device_id", "is_deleted"),
        Index("ix_point_type_active", "point_type", "is_deleted"),
    )

    # ---- 只读派生属性（供 API 出参，避免前端只看到裸 device_id）----
    @property
    def device_code(self) -> str | None:
        return self.device.device_code if self.device is not None else None

    @property
    def device_name(self) -> str | None:
        return self.device.device_name if self.device is not None else None
