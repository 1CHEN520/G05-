"""测点 API：查询 / 新增 / 改名（带影响面）/ 软删 / 版本史。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from g05.api.deps import get_db, get_operator
from g05.audit.service import AuditService
from g05.core.logging import get_logger
from g05.models.device import Device
from g05.models.measure_point import MeasurePoint
from g05.schemas.dto import (
    Page,
    PointCreate,
    PointOut,
    PointRename,
    RenameResult,
)
from g05.services.point_service import (
    PointConflictError,
    PointNotFoundError,
    list_points,
    rename_point,
    soft_delete_point,
)

router = APIRouter(prefix="/points", tags=["测点"])
log = get_logger("api.points")


@router.get("", response_model=Page[PointOut], summary="分页查询测点")
def query_points(
    db: Session = Depends(get_db),
    device_id: int | None = Query(None),
    point_type: str | None = Query(None),
    keyword: str | None = Query(None),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    items, total = list_points(
        db,
        device_id=device_id,
        point_type=point_type,
        keyword=keyword,
        include_deleted=include_deleted,
        page=page,
        size=size,
    )
    return Page[PointOut](items=items, total=total, page=page, size=size)


@router.get("/{point_id}", response_model=PointOut, summary="测点详情")
def get_point(point_id: int, db: Session = Depends(get_db)):
    point = db.get(MeasurePoint, point_id)
    if point is None:
        raise HTTPException(404, f"测点 {point_id} 不存在")
    return point


@router.post("", response_model=PointOut, status_code=201, summary="新增测点")
def create_point(
    payload: PointCreate,
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
):
    device = db.get(Device, payload.device_id)
    if device is None or device.is_deleted:
        raise HTTPException(400, f"设备 {payload.device_id} 不存在（外键约束）")

    exists = db.execute(
        select(MeasurePoint).where(MeasurePoint.point_code == payload.point_code)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"测点编号 {payload.point_code} 已存在")

    point = MeasurePoint(**payload.model_dump())
    point._g05_skip_audit = True  # 下方显式写带 reason 的审计  # noqa: SLF001
    db.add(point)
    db.flush()

    AuditService.record(
        db,
        table_name="measure_point",
        action="INSERT",
        operator=operator,
        record_id=point.id,
        business_key=point.point_code,
        new=payload.model_dump(),
        reason="新增测点",
    )
    db.commit()
    return point


@router.post("/{point_id}/rename", response_model=RenameResult, summary="测点改名（返回影响面）")
def rename(
    point_id: int,
    payload: PointRename,
    db: Session = Depends(get_db),
):
    try:
        result = rename_point(
            db,
            point_id,
            payload.new_name,
            operator=payload.operator,
            reason=payload.reason,
        )
    except PointNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PointConflictError as exc:
        raise HTTPException(409, str(exc)) from exc

    return RenameResult(**result)


@router.delete("/{point_id}", summary="软删除测点")
def delete_point(
    point_id: int,
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
):
    try:
        point = soft_delete_point(db, point_id, operator=operator)
    except PointNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "point_id": point.id, "version": point.version, "action": "soft_delete"}


@router.get("/{point_id}/history", summary="测点变更史（审计时间线）")
def point_history(point_id: int, db: Session = Depends(get_db)):
    point = db.get(MeasurePoint, point_id)
    if point is None:
        raise HTTPException(404, f"测点 {point_id} 不存在")

    entries = AuditService.history_of(db, point.point_code, table_name="measure_point")
    return {
        "point_id": point_id,
        "point_code": point.point_code,
        "versions": len(entries),
        "timeline": [
            {
                "id": e.id,
                "action": e.action,
                "operator": e.operator,
                "reason": e.reason,
                "old": e.old_value,
                "new": e.new_value,
                "changed_at": e.changed_at,
            }
            for e in entries
        ],
    }
