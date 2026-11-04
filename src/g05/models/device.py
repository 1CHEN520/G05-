"""设备表（Device）—— 台账主表。

关系：
- Device 1──N MeasurePoint
- Device 1──N Record
- Device 1──N Alarm

外键策略（以 ``docs/02-架构设计.md`` 的"外键与级联策略"表为准）：

======================  ==========  ====================================================
关系                    ondelete    理由
======================  ==========  ====================================================
measure_point.device_id  RESTRICT   有测点的设备不允许物理删除（资产保护），业务走软删
record.device_id         CASCADE    设备物理删除时历史记录一并清理
alarm.device_id          CASCADE    同上
======================  ==========  ====================================================

注意：``ondelete`` 只在**物理删除**时生效。业务层的"删除"一律走软删
（``is_deleted=True``），两者不要混谈。
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
    """设备台账主表。

    ``device_code`` 是业务唯一键：导入按此判定新增/更新，且**软删不释放编号**
    （编号一旦使用不可复用，符合工业资产编码规范）。
    """

    __tablename__ = "device"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    device_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    device_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    # 运行状态：运行 / 停机 / 检修 / 报废
    # server_default 兜住绕过 ORM 的裸 SQL 写入（C1 批量导入可能走 sqlite3 原生连接），
    # 否则裸 SQL 插入会因 NOT NULL 先失败，掩盖真正该报的错（如外键违例）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="运行", server_default="运行")

    # ---- 关系 ----
    # passive_deletes=True：物理删除交给数据库外键执行，ORM 不逐条加载/删除子表。
    # 否则删一台设备会把它的全部测点与记录拉进内存，量大时直接拖垮进程。
    points: Mapped[list[MeasurePoint]] = relationship(
        back_populates="device",
        passive_deletes=True,
        lazy="selectin",
    )
    records: Mapped[list[Record]] = relationship(
        back_populates="device",
        passive_deletes=True,
        lazy="select",
    )
    alarms: Mapped[list[Alarm]] = relationship(
        back_populates="device",
        passive_deletes=True,
        lazy="select",
    )

    __table_args__ = (
        # 高频查询：按类型+状态筛选设备列表
        Index("ix_device_type_status", "device_type", "status"),
        # 列表页默认过滤 is_deleted；单列基数只有 2，必须与高频过滤列组合才有意义
        Index("ix_device_type_active", "device_type", "is_deleted"),
    )

    # 字段顺序与 schemas/dto.py 的 DeviceOut 严格一致（A 在第 1 周冻结的接口契约）。
    # 注意 pydantic 的字段顺序是"基类字段在前、子类字段在后"：
    # DeviceBase(device_code..status) -> DeviceOut(id, version, is_deleted, created_at, updated_at)
    __dto_fields__ = (
        "device_code",
        "device_name",
        "device_type",
        "location",
        "status",
        "id",
        "version",
        "is_deleted",
        "created_at",
        "updated_at",
    )
