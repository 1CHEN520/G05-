"""设备表（Device）—— 台账主表。

关系：
- Device 1──N MeasurePoint
- Device 1──N Record
- Device 1──N Alarm
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from g05.models.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from g05.models.alarm import Alarm
    from g05.models.measure_point import MeasurePoint
    from g05.models.record import Record


class Device(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "device"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 设备编号：业务唯一键，导入按此判定新增/更新
    device_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    device_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="运行")

    # ---- 关系 ----
    # 资产保护：不使用 delete-orphan，物理删除设备时由数据库外键 RESTRICT 拦截
    # 业务层的"删除"一律走软删（is_deleted=True）
    points: Mapped[list[MeasurePoint]] = relationship(
        back_populates="device",
        passive_deletes=True,
        lazy="selectin",
    )
    records: Mapped[list[Record]] = relationship(
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True
    )
    alarms: Mapped[list[Alarm]] = relationship(
        back_populates="device", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # 复合索引：按类型+状态筛选设备列表是高频查询
        Index("ix_device_type_status", "device_type", "status"),
    )
