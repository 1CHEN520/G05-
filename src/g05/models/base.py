"""ORM 基类与通用 Mixin。

约定：
- 所有表主键为自增整型 ``id``
- 所有表带 ``created_at`` / ``updated_at``（UTC）
- 业务表带软删标记 ``is_deleted`` 与版本号 ``version``（改名不删除）
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """全局声明式基类。"""

    def to_dict(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self) -> str:  # pragma: no cover
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class TimestampMixin:
    """创建/更新时间，由数据库默认值兜底，避免应用层时钟漂移。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class SoftDeleteMixin:
    """软删 + 版本号。

    改名不删除：把旧测点置 ``is_deleted=1``，新记录 ``version+1``，
    血缘查询沿 ``superseded_by`` 串起版本链。
    """

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 被哪个新版本替代（自引用外键，由具体表定义）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
