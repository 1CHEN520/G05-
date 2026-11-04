"""ORM 基类与通用 Mixin。

约定（全项目统一，模型层只描述结构，不写业务逻辑）：

- 所有表主键为自增整型 ``id``
- 所有表带 ``created_at`` / ``updated_at``，一律 UTC 存取
- 资产类表带软删标记 ``is_deleted`` 与版本号 ``version``
- 字段名一旦定下就是契约：C1 的 Excel 列、C2 的规则、C4 的 DTO 全按此对齐

设计依据：
- ``docs/02-架构设计.md``（A 维护，ER 图与外键策略的权威出处）
- ``docs/adr/ADR-001-orm-and-sqlite.md``（选型）
- ``docs/adr/ADR-002-soft-delete-versioning.md``（软删与版本）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from sqlalchemy import Boolean, DateTime, Integer, Text, event, func, text, true
from sqlalchemy.orm import DeclarativeBase, Mapped, Mapper, declared_attr, mapped_column
from sqlalchemy.types import TypeDecorator

__all__ = [
    "Base",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UTCDateTime",
    "alive_filter",
    "utcnow",
]


def utcnow() -> datetime:
    """当前 UTC 时间（带时区信息）。

    严禁使用 ``datetime.now()``：那是本地时间，跨时区与夏令时会出鬼。
    """
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """始终以 UTC 存取的时间列。

    SQLite 没有原生时区类型，``DateTime(timezone=True)`` 存进去的是"去掉时区的字符串"，
    再读出来就是一个 naive 对象，调用方若当成本地时间用就会出现"差 8 小时"。

    这里统一做两层处理：

    1. 写入前：任意 tz 的时间一律折算成 UTC，并去掉 tzinfo 存朴素时间
    2. 读出后：补回 ``timezone.utc``，保证内存里的 datetime 永远带时区
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        # SQLite 侧统一存朴素 UTC；时区信息由本装饰器在读写两端补齐
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # 调用方传了本地时间：按 UTC 解释并立即折算，避免静默错 8 小时
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """全局声明式基类。

    ``to_dict()`` 的字段顺序由子类的 ``__dto_fields__`` 决定，
    需与 ``src/g05/schemas/dto.py``（A 维护）的 DTO 字段顺序保持一致。
    """

    #: to_dict() 输出顺序；为空时退化为表列顺序
    __dto_fields__: ClassVar[tuple[str, ...]] = ()

    def to_dict(self) -> dict[str, Any]:
        """序列化为普通 dict，供审计快照与接口出参复用。"""
        fields = self.__dto_fields__ or tuple(c.name for c in self.__table__.columns)
        return {name: getattr(self, name) for name in fields}

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class TimestampMixin:
    """创建/更新时间（UTC）。

    应用层 default 保证 ORM 写入时有值，server_default 兜住绕过 ORM 的裸 SQL 写入
    （例如 C1 的批量导入若走 sqlite3 原生连接）。

    缺 server_default 的后果很隐蔽：裸 SQL 插入会因 NOT NULL 先失败，
    掩盖真正该报的错（比如外键违例）。
    """

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class SoftDeleteMixin:
    """软删 + 版本号（改名不删行）。

    四个字段一组，语义见 ADR-002：

    - ``is_deleted``    软删标记，默认 False
    - ``deleted_at``    软删时刻，由 ORM 自动补齐
    - ``version``       版本号，**由 ORM 自动 +1，业务层禁止手动改**
    - ``change_note``   变更原因（与审计表的 reason 对应）

    版本号用 SQLAlchemy 原生的 ``version_id_col`` 实现：一来改字段就自动 +1，
    业务层不可能"忘了 +1"；二来白送乐观锁，避免两个人同时改一行互相覆盖。

    兼容性：业务层若仍习惯性写 ``obj.version += 1``，其结果与自动自增一致
    （不会跳号），因此存量代码迁移过来不会把版本号改乱——但**新代码不要再手动 +1**。
    """

    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    @declared_attr
    def __mapper_args__(cls) -> dict[str, Any]:
        """把 version 注册为乐观锁版本列。

        ``declared_attr`` 保证每个子类拿到的是自己那份 version 列，而不是 Mixin 的共享对象。
        """
        return {"version_id_col": cls.version}

    @property
    def alive(self) -> bool:
        """未软删为 True。"""
        return not self.is_deleted


def alive_filter(model: type[Any], include_deleted: bool = False) -> Any:
    """构造"默认过滤已软删行"的查询条件。

    服务层（C4）的列表查询统一走这里，避免每个接口各写一遍而漏掉一处：

    .. code-block:: python

        stmt = select(MeasurePoint).where(alive_filter(MeasurePoint, include_deleted=False))

    :param model: 带 SoftDeleteMixin 的模型类
    :param include_deleted: True 时不过滤（查看回收站场景）
    """
    if include_deleted:
        return true()
    return model.is_deleted.is_(False)


@event.listens_for(Mapper, "before_update")
def _stamp_deleted_at(_mapper: Any, _connection: Any, target: Any) -> None:
    """软删时自动补 ``deleted_at``。

    监听全局 Mapper 事件而非在每个模型上重复挂：新表只要继承了 SoftDeleteMixin
    就自动生效，不需要记得回来补一行注册代码。

    之所以不用 ``default``/``onupdate``：``deleted_at`` 只在 ``is_deleted``
    由 False 变 True 时才该有值，与另一列的条件相关，列级钩子表达不了。
    """
    if not isinstance(target, SoftDeleteMixin):
        return
    if target.is_deleted and target.deleted_at is None:
        target.deleted_at = utcnow()
