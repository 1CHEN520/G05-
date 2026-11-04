"""告警表（Alarm）—— 设备/测点的异常事件。

外键策略（以 ``docs/02-架构设计.md`` 为准）：``device_id`` / ``point_id`` 均 CASCADE ——
设备物理删除时其历史告警一并清理。

告警级别：INFO / WARN / CRITICAL（停机告警直达，永不聚合）。
``recovered_at`` 为 NULL 表示告警仍在持续。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from g05.models.base import Base, TimestampMixin, UTCDateTime, utcnow

if TYPE_CHECKING:
    from g05.models.device import Device
    from g05.models.measure_point import MeasurePoint


class Alarm(Base, TimestampMixin):
    """告警记录。"""

    __tablename__ = "alarm"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True
    )
    point_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("measure_point.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 级别：INFO / WARN / CRITICAL
    alarm_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    alarm_msg: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default="CURRENT_TIMESTAMP", index=True
    )
    # 恢复时间，NULL 表示仍在告警中
    recovered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    device: Mapped[Device] = relationship(back_populates="alarms", lazy="select")
    point: Mapped[MeasurePoint] = relationship(back_populates="alarms", lazy="select")

    __table_args__ = (
        # 查"当前仍在告警中"的记录：alarm_level + recovered_at IS NULL
        Index("ix_alarm_active", "alarm_level", "recovered_at"),
        # 按设备查告警历史
        Index("ix_alarm_device_time", "device_id", "triggered_at"),
    )

    __dto_fields__ = (
        "id",
        "device_id",
        "point_id",
        "alarm_level",
        "alarm_msg",
        "triggered_at",
        "recovered_at",
        "created_at",
        "updated_at",
    )
