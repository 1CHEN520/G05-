"""审计服务：应用层唯一写入口 + SQLAlchemy 自动埋点。

两条路径：
1. **显式审计**：业务代码调用 ``AuditService.record(...)``，用于需要变更原因的语义化操作（改名、软删）
2. **自动审计**：挂到 Session 事件上，任何 flush 的 INSERT/UPDATE/DELETE 都落审计

注意：自动审计只记录"发生了什么"，不记录"为什么"；改名等关键操作应显式补充 reason。

红线（不可违反）：
- 本模块**不提供**任何修改/删除审计记录的方法（连私有方法都不留），审计表是 append-only
- ``before_flush`` 里只允许 ``session.add()``，绝不允许 ``commit()`` / ``flush()``
- 旧值只能取自"对象加载时的快照"，不能在 flush 时现取（那时属性已被改过了）
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from g05.core.logging import get_logger
from g05.models.audit_log import AuditLog
from g05.models.base import Base

log = get_logger("audit.service")

# 需要自动审计的表
_AUDITED_TABLES = {"device", "measure_point", "record", "alarm"}

# 业务键字段候选：按优先级取第一个非空的
_BUSINESS_KEY_FIELDS = ("point_code", "device_code")

# 实例级标记：业务层已显式写审计时置位，自动埋点据此跳过
_SKIP_FLAG = "_g05_skip_audit"

# 加载时快照的存放属性
_SNAPSHOT_ATTR = "_g05_old_snapshot"

# 埋点是否已安装（保证幂等：应用启动 + 测试夹具各调一次也不会重复埋点）
_installed = False


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _snapshot(obj: Base) -> dict[str, Any]:
    """把对象当前状态拍成可 JSON 化的字典。"""
    return {c.name: _jsonable(getattr(obj, c.name, None)) for c in obj.__table__.columns}


def _business_key(obj: Base) -> str | None:
    """取业务键（跨版本追同一实体的稳定标识）。

    不用 ``record_id``：软删后重建同编号设备会得到新 id，历史就断了。
    """
    for field in _BUSINESS_KEY_FIELDS:
        value = getattr(obj, field, None)
        if value:
            return str(value)
    return None


def _is_soft_delete(obj: Base, old: dict[str, Any] | None) -> bool:
    """判断这次变更是不是软删（``is_deleted`` 由 False 变 True）。

    快照缺失时不做判断，保守地记 UPDATE。
    """
    if not old:
        return False
    was = old.get("is_deleted")
    if was is None:
        return False
    now = getattr(obj, "is_deleted", None)
    return bool(was) is False and bool(now) is True


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
        """追加一条审计记录（尚未入库，随本次事务一起提交）。"""
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
        """列表查询：按时间倒序（最近的在前）。"""
        stmt = select(AuditLog).order_by(AuditLog.changed_at.desc(), AuditLog.id.desc())
        stmt = _apply_filters(
            stmt,
            table_name=table_name,
            business_key=business_key,
            operator=operator,
            action=action,
            start=start,
            end=end,
        )
        stmt = stmt.limit(limit).offset(offset)
        return list(session.execute(stmt).scalars())

    @staticmethod
    def count(
        session: Session,
        *,
        table_name: str | None = None,
        business_key: str | None = None,
        operator: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        """符合条件的总条数（不受分页影响）。"""
        stmt = select(func.count()).select_from(AuditLog)
        stmt = _apply_filters(
            stmt,
            table_name=table_name,
            business_key=business_key,
            operator=operator,
            action=action,
            start=start,
            end=end,
        )
        return int(session.execute(stmt).scalar_one())

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


def _apply_filters(stmt, *, table_name, business_key, operator, action, start, end):  # noqa: ANN001, ANN202
    """给查询语句追加过滤条件（count 与 list 共用，保证口径一致）。"""
    if table_name:
        stmt = stmt.where(AuditLog.table_name == table_name)
    if business_key:
        stmt = stmt.where(AuditLog.business_key == business_key)
    if operator:
        stmt = stmt.where(AuditLog.operator == operator)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if start:
        stmt = stmt.where(AuditLog.changed_at >= _as_utc_naive(start))
    if end:
        stmt = stmt.where(AuditLog.changed_at <= _as_utc_naive(end))
    return stmt


def _as_utc_naive(value: datetime) -> datetime:
    """把带时区时间统一成 UTC 朴素时间再交给 SQLite 比较。

    SQLite 存的是 UTC 墙钟时间，如果直接拿 ``+08:00`` 的入参去比，
    边界会错 8 小时——时间范围查询会悄悄查错一段区间。
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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


def _consume_skip_flag(obj: Any) -> bool:
    """读取并清掉"业务层已显式写审计"的标记。

    new / dirty / deleted 三个分支都要调用它，漏一个就会产生重复审计。
    """
    if getattr(obj, _SKIP_FLAG, False):
        setattr(obj, _SKIP_FLAG, False)
        return True
    return False


def _install() -> None:
    """把自动埋点挂到 SQLAlchemy 的 Session 类上。

    为什么挂 **Session 类** 而不是 sessionmaker 实例：
    sessionmaker 实例的事件只对该实例产出的 Session 生效，
    而测试里常用 ``Session(bind=engine)`` 另开 Session——那种情况会漏埋点。
    类级监听对所有 Session 生效，配合"每次 flush 只处理一次"的护栏，
    既不会漏，也不会重复。
    """
    global _installed
    if _installed:
        return
    _installed = True

    @event.listens_for(Session, "before_flush")
    def _before_flush(session: Session, flush_context, instances) -> None:  # noqa: ANN001
        """flush 之前：判断哪些对象要记审计。

        这里**只**做判断与登记。真正的写库放在 after_flush，
        因为 INSERT 的自增主键与列默认值在 flush 之前还没解析出来。
        """
        operator = _OPERATOR["current"]

        for obj in list(session.new):
            if obj.__tablename__ not in _AUDITED_TABLES:
                continue
            # 业务层已显式写审计（带 reason），跳过自动埋点避免重复
            if _consume_skip_flag(obj):
                continue
            _mark_pending(session, obj, "INSERT", operator)

        for obj in list(session.dirty):
            if obj.__tablename__ not in _AUDITED_TABLES:
                continue
            if _consume_skip_flag(obj):
                continue
            if not session.is_modified(obj, include_collections=False):
                continue
            _mark_pending(session, obj, "UPDATE", operator)

        for obj in list(session.deleted):
            if obj.__tablename__ not in _AUDITED_TABLES:
                continue
            if _consume_skip_flag(obj):
                continue
            # 硬删的旧值要在删除生效**之前**拍：flush 之后对象属性会被重置，
            # 再去取值既可能拿到过期的，也可能直接抛 ObjectDeletedError
            _mark_pending(session, obj, "DELETE", operator, old=_snapshot(obj))

    @event.listens_for(Session, "after_flush")
    def _after_flush(session: Session, flush_context) -> None:  # noqa: ANN001
        """flush 之后：把审计记录真正写进库，并刷新"下次变更的旧值"。

        放在这里有两个原因：
        1. INSERT 的自增主键与列默认值（version / created_at 等）此刻才解析出来，
           在 before_flush 拿到的全是 None，写进审计就成了假数据
        2. 落库成功后要把当前值变成下一次的"旧值"，
           否则同一 session 里连续改两次，第二次的 old_value 仍是第一次之前的老快照
        """
        pending = _take_pending(session)
        deleted_ids: set[int] = set()
        for action, obj, operator, old in pending:
            if action == "DELETE":
                deleted_ids.add(id(obj))
                session.add(
                    AuditLog.build(
                        table_name=obj.__tablename__,
                        action="DELETE",
                        operator=operator,
                        record_id=getattr(obj, "id", None),
                        business_key=_business_key(obj),
                        old=old,
                    )
                )
                continue

            if action == "UPDATE":
                old = getattr(obj, _SNAPSHOT_ATTR, None)
                if _is_soft_delete(obj, old):
                    action = "SOFT_DELETE"

            session.add(
                AuditLog.build(
                    table_name=obj.__tablename__,
                    action=action,
                    operator=operator,
                    record_id=getattr(obj, "id", None),
                    business_key=_business_key(obj),
                    old=old,
                    new=_snapshot(obj),
                )
            )

        # 刷新快照：本次落库后的值，就是下次修改的"旧值"
        for obj in list(session.identity_map.values()):
            if obj.__tablename__ not in _AUDITED_TABLES:
                continue
            if id(obj) in deleted_ids:
                # 已删除的对象属性会被重置/驱逐，不能再拍快照
                continue
            setattr(obj, _SNAPSHOT_ATTR, _snapshot(obj))

    @event.listens_for(Session, "loaded_as_persistent")
    def _capture_old(session: Session, instance) -> None:  # noqa: ANN001
        """对象加载后拍一份快照，UPDATE 时作为 old_value。

        这是整个审计可信度的命门：只有"加载时"属性还是库里的原值。
        在 flush 时取就晚了——那时业务代码已经把属性改掉了，取到的是新值。
        """
        if instance.__tablename__ in _AUDITED_TABLES:
            setattr(instance, _SNAPSHOT_ATTR, _snapshot(instance))


def _mark_pending(
    session: Session,
    obj: Base,
    action: str,
    operator: str,
    *,
    old: dict[str, Any] | None = None,
) -> None:
    """登记"这次 flush 要记一条审计"。

    同一个对象在同一轮 flush 里只登记一次，防止 ORM 的多次 flush 造成重复审计。
    """
    pending = _pending_of(session)
    key = id(obj)
    if key in pending:
        return
    pending[key] = (action, obj, operator, old)


def _pending_of(session: Session) -> dict[int, tuple[str, Base, str, dict[str, Any] | None]]:
    """取出（并按需初始化）当前 Session 的待写审计登记表。"""
    return session.info.setdefault("_g05_pending_audit", {})


def _take_pending(session: Session) -> list[tuple[str, Base, str, dict[str, Any] | None]]:
    """取走本轮待写审计登记，并清空登记（防止下一轮 flush 重复写）。"""
    store = session.info.get("_g05_pending_audit") or {}
    items = list(store.values())
    store.clear()
    return items


def install_auto_audit() -> None:
    """安装自动埋点。

    埋点在模块导入时就已经挂上（见本文件末尾），所以这个函数是**幂等**的：
    应用启动、测试夹具重复调用都不会导致重复埋点、重复审计。
    """
    _install()
    log.info("auto audit installed", extra={"extra_fields": {"tables": sorted(_AUDITED_TABLES)}})


def parse_snapshot(raw: str | None) -> dict[str, Any] | None:
    """把 JSON 字符串还原成字典；解析失败时原样包一层，便于排查。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


# 模块导入即安装自动埋点——业务代码不显式调用任何审计 API 也会留痕。
# 这也是"自动埋点生效"这条验收标准能够成立的前提。
_install()
