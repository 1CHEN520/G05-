"""告警表（Alarm）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from g05.models.base import Base, TimestampMixin, utcnow

if TYPE_CHECKING:
    from g05.models.device import Device
    from g05.models.measure_point import MeasurePoint


class Alarm(Base, TimestampMixin):
    __tablename__ = "alarm"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device.id", ondelete="CASCADE"), nullable=False, index=True
    )
    point_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("measure_point.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 级别：INFO / WARN / CRITICAL（停机告警直达，永不聚合）
    alarm_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    alarm_msg: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    # 恢复时间，NULL 表示仍在告警中
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="alarms", lazy="select")
    point: Mapped["MeasurePoint"] = relationship(back_populates="alarms", lazy="select")

    __table_args__ = (
        Index("ix_alarm_active", "alarm_level", "recovered_at"),
        Index("ix_alarm_device_time", "device_id", "triggered_at"),
    )
