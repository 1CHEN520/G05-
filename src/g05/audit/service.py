"""审计服务：应用层唯一写入口 + SQLAlchemy 自动埋点。

两条路径：
1. **显式审计**：业务代码调用 ``AuditService.record(...)``，用于需要变更原因的语义化操作（改名、软删）
2. **自动审计**：``install_auto_audit()`` 挂到 Session 事件上，任何 flush 的 INSERT/UPDATE/DELETE 都落审计

注意：自动审计只记录"发生了什么"，不记录"为什么"；改名等关键操作应显式补充 reason。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from g05.core.logging import get_logger
from g05.models.audit_log import AuditLog
from g05.models.base import Base

log = get_logger("audit.service")

# 需要自动审计的表
_AUDITED_TABLES = {"device", "measure_point", "record", "alarm"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _snapshot(obj: Base) -> dict[str, Any]:
    return {c.name: _jsonable(getattr(obj, c.name, None)) for c in obj.__table__.columns}


def _business_key(obj: Base) -> str | None:
    return getattr(obj, "point_code", None) or getattr(obj, "device_code", None)


class AuditService:
    """审计读写。写只走 append，永不 update/delete。"""

    @staticmethod
    def record(
        session: Session,
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
    ) -> AuditLog:
        entry = AuditLog.build(
            table_name=table_name,
            action=action,
            operator=operator,
            record_id=record_id,
            business_key=business_key,
            old=old,
            new=new,
            reason=reason,
            trace_id=trace_id,
        )
        session.add(entry)
        log.info(
            "audit.write",
            extra={
                "extra_fields": {
                    "table": table_name,
                    "action": action,
                    "operator": operator,
                    "business_key": business_key,
                }
            },
        )
        return entry

    @staticmethod
    def query(
        session: Session,
        *,
        table_name: str | None = None,
        business_key: str | None = None,
        operator: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.changed_at.desc(), AuditLog.id.desc())
        if table_name:
            stmt = stmt.where(AuditLog.table_name == table_name)
        if business_key:
            stmt = stmt.where(AuditLog.business_key == business_key)
        if operator:
            stmt = stmt.where(AuditLog.operator == operator)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if start:
            stmt = stmt.where(AuditLog.changed_at >= start)
        if end:
            stmt = stmt.where(AuditLog.changed_at <= end)
        stmt = stmt.limit(limit).offset(offset)
        return list(session.execute(stmt).scalars())

    @staticmethod
    def history_of(session: Session, business_key: str, table_name: str | None = None) -> list[AuditLog]:
        """某实体（按业务键）的完整变更史，按时间正序，用于时间线展示。"""
        stmt = (
            select(AuditLog)
            .where(AuditLog.business_key == business_key)
            .order_by(AuditLog.changed_at.asc(), AuditLog.id.asc())
        )
        if table_name:
            stmt = stmt.where(AuditLog.table_name == table_name)
        return list(session.execute(stmt).scalars())


# ------------------------------------------------------------
# SQLAlchemy 自动埋点
# ------------------------------------------------------------
_OPERATOR: dict[str, str] = {"current": "system"}


@contextmanager
def audit_as(operator: str) -> Iterator[None]:
    """临时切换当前操作人（通常由 API 中间件包裹请求）。"""
    previous = _OPERATOR["current"]
    _OPERATOR["current"] = operator or "anonymous"
    try:
        yield
    finally:
        _OPERATOR["current"] = previous


def _install(session_factory) -> None:  # noqa: ANN001
    @event.listens_for(session_factory, "before_flush")
    def _before_flush(session: Session, flush_context, instances) -> None:  # noqa: ANN001
        operator = _OPERATOR["current"]
        for obj in session.new:
            if obj.__tablename__ not in _AUDITED_TABLES:
                continue
            # 业务层已显式写审计（带 reason），跳过自动埋点避免重复
            if getattr(obj, "_g05_skip_audit", False):
                obj._g05_skip_audit = False  # noqa: SLF001
                continue
            session.add(
                AuditLog.build(
                    table_name=obj.__tablename__,
                    action="INSERT",
                    operator=operator,
                    business_key=_business_key(obj),
                    new=_snapshot(obj),
                )
            )

        for obj in session.dirty:
            if obj.__tablename__ not in _AUDITED_TABLES:
                continue
            # 业务层已显式写审计（如 RENAME），跳过自动埋点避免重复
            if getattr(obj, "_g05_skip_audit", False):
                obj._g05_skip_audit = False  # noqa: SLF001
                continue
            if not session.is_modified(obj, include_collections=False):
                continue
            old = getattr(obj, "_g05_old_snapshot", None)
            session.add(
                AuditLog.build(
                    table_name=obj.__tablename__,
                    action="UPDATE",
                    operator=operator,
                    record_id=getattr(obj, "id", None),
                    business_key=_business_key(obj),
                    old=old,
                    new=_snapshot(obj),
                )
            )

        for obj in session.deleted:
            if obj.__tablename__ not in _AUDITED_TABLES:
                continue
            if getattr(obj, "_g05_skip_audit", False):
                obj._g05_skip_audit = False  # noqa: SLF001
                continue
            session.add(
                AuditLog.build(
                    table_name=obj.__tablename__,
                    action="DELETE",
                    operator=operator,
                    record_id=getattr(obj, "id", None),
                    business_key=_business_key(obj),
                    old=_snapshot(obj),
                )
            )

    @event.listens_for(session_factory, "loaded_as_persistent")
    def _capture_old(session: Session, instance) -> None:  # noqa: ANN001
        """对象加载后拍一份快照，UPDATE 时作为 old_value。"""
        if instance.__tablename__ in _AUDITED_TABLES:
            instance._g05_old_snapshot = _snapshot(instance)  # noqa: SLF001


def install_auto_audit() -> None:
    """在 Engine 初始化后调用一次。"""
    from g05.core.db import get_session_factory

    _install(get_session_factory())
    log.info("auto audit installed", extra={"extra_fields": {"tables": sorted(_AUDITED_TABLES)}})


def parse_snapshot(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
