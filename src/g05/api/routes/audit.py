"""审计 API：查询、时间线、导出、防篡改验证。

红线：本模块**不提供**任何修改/删除审计记录的接口。
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from g05.api.deps import get_db
from g05.audit.service import AuditService, parse_snapshot
from g05.models.audit_log import AuditLog
from g05.schemas.dto import AuditOut

router = APIRouter(prefix="/audit", tags=["审计"])

# 错误详情截断长度：SQLite 的错误串可能很长，前端展示会撑破布局
_DETAIL_MAX_LEN = 160


@router.get("", summary="审计查询")
def query_audit(
    db: Session = Depends(get_db),
    table_name: str | None = Query(None),
    business_key: str | None = Query(None),
    operator: str | None = Query(None),
    action: str | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """按表 / 业务键 / 操作人 / 动作 / 时间范围过滤审计记录。

    ``total`` 是**符合条件的总条数**（不受分页影响），前端分页要用它。
    """
    entries = AuditService.query(
        db,
        table_name=table_name,
        business_key=business_key,
        operator=operator,
        action=action,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    total = AuditService.count(
        db,
        table_name=table_name,
        business_key=business_key,
        operator=operator,
        action=action,
        start=start,
        end=end,
    )
    return {
        "total": total,
        "count": len(entries),
        "limit": limit,
        "offset": offset,
        "items": [AuditOut.model_validate(e).model_dump() for e in entries],
    }


@router.get("/export", summary="审计导出 CSV")
def export_audit(
    db: Session = Depends(get_db),
    table_name: str | None = Query(None),
    business_key: str | None = Query(None),
    operator: str | None = Query(None),
    action: str | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    limit: int = Query(5000, ge=1, le=50000),
):
    """导出审计记录为 CSV（UTF-8 BOM，Excel 直接打开中文不乱码）。

    过滤条件与 ``GET /api/audit`` 保持一致，避免"页面能筛、导出不能筛"。
    """
    entries = AuditService.query(
        db,
        table_name=table_name,
        business_key=business_key,
        operator=operator,
        action=action,
        start=start,
        end=end,
        limit=limit,
    )

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM，Excel 打开中文不乱码
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "table_name",
            "record_id",
            "business_key",
            "action",
            "operator",
            "reason",
            "trace_id",
            "changed_at",
            "old_value",
            "new_value",
        ]
    )
    for e in entries:
        writer.writerow(
            [
                e.id,
                e.table_name,
                e.record_id,
                e.business_key,
                e.action,
                e.operator,
                e.reason,
                e.trace_id,
                e.changed_at.isoformat() if e.changed_at else "",
                e.old_value,
                e.new_value,
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit_export.csv"'},
    )


@router.get("/timeline", summary="某实体的变更时间线")
def timeline_audit(
    db: Session = Depends(get_db),
    business_key: str = Query(..., description="业务键，如 device_code / point_code"),
    table_name: str | None = Query(None),
):
    """按业务键返回完整变更史（时间正序），每条附带字段级 diff。

    用 ``business_key`` 而不是 ``record_id``：软删后重建同编号设备会得到新的
    自增 id，按 record_id 查历史会断链。
    """
    entries = AuditService.history_of(db, business_key, table_name=table_name)
    items = []
    for e in entries:
        item = AuditOut.model_validate(e).model_dump()
        item["diff"] = AuditLog.diff(
            parse_snapshot(e.old_value),
            parse_snapshot(e.new_value),
        )
        items.append(item)
    return {
        "business_key": business_key,
        "total": len(items),
        "items": items,
    }


@router.post("/verify-immutable", summary="验证审计不可篡改（尝试 UPDATE/DELETE 应被拒绝）")
def verify_immutable():
    """现场答辩演示用：直接对审计表发 UPDATE/DELETE，数据库触发器应 ABORT。

    四个关键点：
    1. 用**独立 session**，不污染请求上下文
    2. 每次篡改尝试都用 SAVEPOINT 圈起来，失败只回滚到保存点，
       会话不会进入 aborted 状态，后续查询照常可用
    3. 空表时先插入一条探针记录再试篡改——否则 UPDATE 匹配 0 行会"假通过"，
       误报成"没拦住"。探针随整个事务一起回滚，不会留在库里
    4. 探针插入失败（库被别的写事务锁住）不能抛 500，
       要降级成"本项未能验证"并说明原因，而不是让整个接口挂掉
    """
    from g05.core.db import get_session_factory

    checks: list[dict[str, object]] = []
    probe_session = get_session_factory()()

    try:
        # 空表守卫：确保至少有一条记录可被篡改，否则测的是"空操作"不是"不可篡改"
        probe_id = probe_session.execute(text("SELECT MIN(id) FROM audit_log")).scalar()
        if probe_id is None:
            try:
                probe_session.execute(
                    text(
                        "INSERT INTO audit_log (table_name, action, operator, changed_at) "
                        "VALUES ('_verify_probe', 'PROBE', 'verify-immutable', CURRENT_TIMESTAMP)"
                    )
                )
                probe_session.flush()
                probe_id = probe_session.execute(text("SELECT MIN(id) FROM audit_log")).scalar()
            except Exception as exc:  # noqa: BLE001
                # 库被其他写事务锁住等情形：降级说明，不让接口整体 500
                probe_session.rollback()
                checks.append(
                    {
                        "operation": "空表探针",
                        "blocked": False,
                        "detail": f"无法插入探针记录，本次未能执行篡改验证: {str(exc)[:120]}",
                    }
                )
                probe_id = None

        if probe_id is not None:
            for sql, label in (
                (
                    "UPDATE audit_log SET operator='hacker' "
                    "WHERE id=(SELECT MIN(id) FROM audit_log)",
                    "UPDATE",
                ),
                (
                    "DELETE FROM audit_log "
                    "WHERE id=(SELECT MIN(id) FROM audit_log)",
                    "DELETE",
                ),
            ):
                savepoint = probe_session.begin_nested()
                try:
                    probe_session.execute(text(sql))
                    # 没被拦截：把这次"篡改成功"也回滚掉，绝不真的改到审计数据
                    savepoint.rollback()
                    checks.append(
                        {"operation": label, "blocked": False, "detail": "未拦截（不合规！）"}
                    )
                except Exception as exc:  # noqa: BLE001
                    # ← 少了 rollback，会话会进入 aborted 状态，后续查询全报错
                    savepoint.rollback()
                    checks.append(
                        {"operation": label, "blocked": True, "detail": str(exc)[:_DETAIL_MAX_LEN]}
                    )
    finally:
        # 探针记录与任何"侥幸篡改成功"的改动，随整个事务一起回滚，库里不留痕
        probe_session.rollback()
        probe_session.close()

    # 应用层防线：AuditService 不应暴露任何改删方法
    forbidden = [
        name
        for name in dir(AuditService)
        if not name.startswith("_") and name.lower() in {"update", "delete", "remove", "destroy"}
    ]
    checks.append(
        {
            "operation": "应用层接口",
            "blocked": not forbidden,
            "detail": (
                "AuditService 未暴露任何 update/delete 方法"
                if not forbidden
                else f"发现危险方法: {forbidden}"
            ),
        }
    )

    all_blocked = all(bool(c["blocked"]) for c in checks)
    return {"immutable": all_blocked, "checks": checks}
