"""记录表（Record）—— 测点采集值。

量级最大（可能千万行），因此：
- 时间戳与 (point_id, ts) 复合索引，支撑区间查询
- 分区/归档策略见 docs/02-架构设计.md
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from g05.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from g05.models.device import Device
    from g05.models.measure_point import MeasurePoint


class Record(Base, TimestampMixin):
    __tablename__ = "record"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True
    )
    point_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("measure_point.id", ondelete="CASCADE"), nullable=False, index=True
    )

    value: Mapped[float] = mapped_column(Float, nullable=False)
    # 采集时刻（设备侧时间），与 created_at（入库时间）区分
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # 数据质量标记：GOOD / BAD / UNCERTAIN
    quality: Mapped[str] = mapped_column(String(16), nullable=False, default="GOOD")

    device: Mapped[Device] = relationship(back_populates="records", lazy="select")
    point: Mapped[MeasurePoint] = relationship(back_populates="records", lazy="select")

    __table_args__ = (
        # 最左前缀：按测点查时间段走此索引
        Index("ix_record_point_ts", "point_id", "ts"),
        Index("ix_record_device_ts", "device_id", "ts"),
    )
