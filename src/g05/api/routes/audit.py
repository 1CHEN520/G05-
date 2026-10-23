"""审计 API：查询、时间线、导出、防篡改验证。"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from g05.api.deps import get_db
from g05.audit.service import AuditService
from g05.schemas.dto import AuditOut

router = APIRouter(prefix="/audit", tags=["审计"])


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
    return {
        "total": len(entries),
        "items": [AuditOut.model_validate(e).model_dump() for e in entries],
    }


@router.get("/export", summary="审计导出 CSV")
def export_audit(
    db: Session = Depends(get_db),
    table_name: str | None = Query(None),
    operator: str | None = Query(None),
    limit: int = Query(5000, ge=1, le=50000),
):
    entries = AuditService.query(db, table_name=table_name, operator=operator, limit=limit)

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM，Excel 打开中文不乱码
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "table_name", "record_id", "business_key", "action", "operator", "reason", "changed_at", "old_value", "new_value"]
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


@router.post("/verify-immutable", summary="验证审计不可篡改（尝试 UPDATE 应被拒绝）")
def verify_immutable(db: Session = Depends(get_db)):
    """现场答辩演示用：直接对审计表发 UPDATE/DELETE，数据库触发器应 ABORT。"""
    checks = []
    for sql, label in (
        ("UPDATE audit_log SET operator='hacker' WHERE id=(SELECT MIN(id) FROM audit_log)", "UPDATE"),
        ("DELETE FROM audit_log WHERE id=(SELECT MIN(id) FROM audit_log)", "DELETE"),
    ):
        try:
            db.execute(text(sql))
            db.commit()
            checks.append({"operation": label, "blocked": False, "detail": "未拦截（不合规！）"})
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            checks.append({"operation": label, "blocked": True, "detail": str(exc)[:160]})

    all_blocked = all(c["blocked"] for c in checks)
    return {"immutable": all_blocked, "checks": checks}
