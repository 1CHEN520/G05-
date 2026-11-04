"""记录表（Record）—— 测点采集值。

量级最大（可能千万行），因此：

- 只保留必要列，不加软删字段：采集数据按保留期归档，不参与资产版本链
- ``(point_id, ts)`` 与 ``(device_id, ts)`` 复合索引支撑时间区间查询
- 归档/分区策略见 ``config/database.yaml`` 的 ``database.archive`` 段

时间语义：``ts`` 是**设备侧采集时刻**（可能迟到、可能乱序），
``created_at`` 是**入库时刻**（见 TimestampMixin）。两者不可混用，
做实时性统计必须用 ``ts``。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from g05.models.base import Base, TimestampMixin, UTCDateTime

if TYPE_CHECKING:
    from g05.models.device import Device
    from g05.models.measure_point import MeasurePoint


class Record(Base, TimestampMixin):
    """采集记录（时序数据）。

    ``ondelete=CASCADE``：测点/设备物理删除时，其采集记录随之清除 ——
    采集值是宿主的行为数据，宿主不存在时它没有任何独立意义。
    业务层的软删不影响本表。
    """

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
    ts: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    # 数据质量标记：GOOD / BAD / UNCERTAIN
    quality: Mapped[str] = mapped_column(
        String(16), nullable=False, default="GOOD", server_default="GOOD"
    )

    # lazy="select"：记录表量级巨大，绝不能预加载，详情页按需单查
    device: Mapped[Device] = relationship(back_populates="records", lazy="select")
    point: Mapped[MeasurePoint] = relationship(back_populates="records", lazy="select")

    __table_args__ = (
        # 最左前缀：按测点查时间段走此索引
        Index("ix_record_point_ts", "point_id", "ts"),
        # 按设备查时间段走此索引
        Index("ix_record_device_ts", "device_id", "ts"),
    )

    __dto_fields__ = (
        "id",
        "device_id",
        "point_id",
        "value",
        "ts",
        "quality",
        "created_at",
        "updated_at",
    )
