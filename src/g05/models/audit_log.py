"""审计日志表（AuditLog）—— append-only，不可篡改。

不可篡改的两道防线：
1. 应用层：只提供 ``append()``，不提供 update/delete
2. 数据库层：SQLite 触发器 ``trg_audit_no_update`` / ``trg_audit_no_delete`` 直接 RAISE(ABORT)

评分红线："密码明文/审计可改 = 该维度 0 分"。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from g05.models.base import Base, utcnow


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # 操作对象
    table_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    record_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 业务键（device_code / point_code），跨版本追同一实体
    business_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # INSERT | UPDATE | DELETE | SOFT_DELETE | RENAME
    action: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    operator: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 变更前后快照（JSON 字符串，SQLite 无原生 JSONB）
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 变更原因与请求链路
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        # 审计查询主路径：按表 + 时间倒序
        Index("ix_audit_table_time", "table_name", "changed_at"),
        Index("ix_audit_operator_time", "operator", "changed_at"),
    )

    @classmethod
    def build(
        cls,
        *,
        table_name: str,
        action: str,
        operator: str,
        record_id: int | None = None,
        business_key: str | None = None,
        old: dict[str, Any] | None = None,
        new: dict[str, Any] | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> "AuditLog":
        """构造一条审计记录（尚未入库）。"""
        import json

        def dump(obj: dict[str, Any] | None) -> str | None:
            if obj is None:
                return None
            return json.dumps(obj, ensure_ascii=False, default=str)

        return cls(
            table_name=table_name,
            record_id=record_id,
            business_key=business_key,
            action=action,
            operator=operator,
            old_value=dump(old),
            new_value=dump(new),
            reason=reason,
            trace_id=trace_id,
        )
