"""设备台账 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from g05.api.deps import get_db, get_operator
from g05.audit.service import AuditService
from g05.models.device import Device
from g05.models.measure_point import MeasurePoint
from g05.schemas.dto import DeviceCreate, DeviceOut, DeviceUpdate, Page
from g05.services.point_service import list_devices

router = APIRouter(prefix="/devices", tags=["设备"])


@router.get("", response_model=Page[DeviceOut], summary="分页查询设备")
def query_devices(
    db: Session = Depends(get_db),
    device_type: str | None = Query(None, description="设备类型过滤"),
    status: str | None = Query(None, description="状态过滤"),
    keyword: str | None = Query(None, description="编号/名称模糊搜索"),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    items, total = list_devices(
        db,
        device_type=device_type,
        status=status,
        keyword=keyword,
        include_deleted=include_deleted,
        page=page,
        size=size,
    )
    return Page[DeviceOut](items=items, total=total, page=page, size=size)


@router.get("/{device_id}", response_model=DeviceOut, summary="设备详情")
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, f"设备 {device_id} 不存在")
    return device


@router.get("/{device_id}/points", summary="设备下的测点清单")
def list_device_points(device_id: int, db: Session = Depends(get_db)):
    stmt = select(MeasurePoint).where(
        MeasurePoint.device_id == device_id, MeasurePoint.is_deleted.is_(False)
    )
    points = list(db.execute(stmt).scalars())
    return {"device_id": device_id, "total": len(points), "items": [p.to_dict() for p in points]}


@router.post("", response_model=DeviceOut, status_code=201, summary="新增设备")
def create_device(
    payload: DeviceCreate,
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
):
    exists = db.execute(
        select(Device).where(Device.device_code == payload.device_code)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"设备编号 {payload.device_code} 已存在")

    device = Device(**payload.model_dump())
    device._g05_skip_audit = True  # 下方显式写带 reason 的审计  # noqa: SLF001
    db.add(device)
    db.flush()

    AuditService.record(
        db,
        table_name="device",
        action="INSERT",
        operator=operator,
        record_id=device.id,
        business_key=device.device_code,
        new=payload.model_dump(),
        reason="新增设备",
    )
    db.commit()
    return device


@router.patch("/{device_id}", response_model=DeviceOut, summary="更新设备")
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
):
    device = db.get(Device, device_id)
    if device is None or device.is_deleted:
        raise HTTPException(404, f"设备 {device_id} 不存在")

    old = device.to_dict()
    changes = payload.model_dump(exclude_unset=True)
    device._g05_skip_audit = True  # 下方显式写带 reason 的审计  # noqa: SLF001
    for key, value in changes.items():
        setattr(device, key, value)
    device.version += 1
    db.flush()

    AuditService.record(
        db,
        table_name="device",
        action="UPDATE",
        operator=operator,
        record_id=device.id,
        business_key=device.device_code,
        old=old,
        new=device.to_dict(),
        reason="更新设备信息",
    )
    db.commit()
    return device


@router.delete("/{device_id}", summary="软删除设备")
def delete_device(
    device_id: int,
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
):
    device = db.get(Device, device_id)
    if device is None or device.is_deleted:
        raise HTTPException(404, f"设备 {device_id} 不存在")

    # 有活跃测点时拒绝删除，保护资产完整性
    active_points = db.execute(
        select(func.count())
        .select_from(MeasurePoint)
        .where(MeasurePoint.device_id == device_id, MeasurePoint.is_deleted.is_(False))
    ).scalar_one()
    if active_points:
        raise HTTPException(409, f"设备下仍有 {active_points} 个活跃测点，请先处理测点")

    old = device.to_dict()
    device._g05_skip_audit = True  # 下方显式写带 reason 的审计  # noqa: SLF001
    device.is_deleted = True
    device.version += 1

    AuditService.record(
        db,
        table_name="device",
        action="SOFT_DELETE",
        operator=operator,
        record_id=device.id,
        business_key=device.device_code,
        old=old,
        new={"is_deleted": True, "version": device.version},
        reason="软删除设备",
    )
    db.commit()
    return {"ok": True, "device_id": device_id, "action": "soft_delete"}
