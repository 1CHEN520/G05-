"""测点表（MeasurePoint）—— 资产目录的核心。

血缘与版本链设计（详见 ADR-002）：

- ``renamed_from`` 保留改名前的历史名称，供审计与影响面回溯
- ``superseded_by`` 自引用指向"接替它的新测点"，串起版本链
- 改名**不删行**：只更新 ``point_name``，``version`` 自动 +1

关键取舍：改名时 ``point_code`` 不变，只改 ``point_name``。
因为血缘边挂在 ``point_code`` 上，编号不变 → 血缘边无需重连，
改名的代价从"重建整张血缘图"降到"更新一行"。

外键策略：

- ``device_id`` → RESTRICT：有测点的设备不允许物理删除（资产保护），业务走软删
- ``superseded_by`` → SET NULL：版本链断裂不阻塞删除
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
    """测点（采集点）台账。

    ``point_code`` 是全局唯一业务键：血缘节点以它作为 ``node_key``。
    软删不释放编号 —— 编号一旦使用不可复用。
    """

    __tablename__ = "measure_point"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # 外键必须建索引（否则级联删除与 join 都要全表扫）
    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("device.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    point_code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True, index=True)
    point_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    point_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)

    # ---- 血缘/版本链 ----
    # 接替本测点的新测点；本行被改取代时由新行回填
    superseded_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("measure_point.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 改名前的历史名称，审计与影响面回溯时展示
    renamed_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 变更原因说明，与审计表的 reason 对应
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 关系 ----
    # lazy="selectin"：测点列表页要带出所属设备编号，用 selectin 一次取回避免 N+1
    device: Mapped[Device] = relationship(back_populates="points", lazy="selectin")
    records: Mapped[list[Record]] = relationship(
        back_populates="point", passive_deletes=True, lazy="select"
    )
    alarms: Mapped[list[Alarm]] = relationship(
        back_populates="point", passive_deletes=True, lazy="select"
    )

    __table_args__ = (
        # 复合索引：按设备查未删测点（列表页最常见的查询）
        Index("ix_point_device_active", "device_id", "is_deleted"),
        # 复合索引：按测点类型查未删测点
        Index("ix_point_type_active", "point_type", "is_deleted"),
    )

    # ---- 只读派生属性（供 API 出参，避免前端只看到裸 device_id）----
    @property
    def device_code(self) -> str | None:
        return self.device.device_code if self.device is not None else None

    @property
    def device_name(self) -> str | None:
        return self.device.device_name if self.device is not None else None

    # 字段顺序与 schemas/dto.py 的 PointOut 严格一致（A 在第 1 周冻结的接口契约）。
    # 注意 pydantic 的字段顺序是"基类字段在前、子类字段在后"：
    # PointBase(device_id..unit) -> PointOut(id, device_code, device_name, version, ...)
    __dto_fields__ = (
        "device_id",
        "point_code",
        "point_name",
        "point_type",
        "unit",
        "id",
        "device_code",
        "device_name",
        "version",
        "is_deleted",
        "superseded_by",
        "renamed_from",
        "change_note",
        "created_at",
        "updated_at",
    )
