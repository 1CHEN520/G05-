"""设备台账 API。

错误码统一走 :mod:`g05.schemas.errors` 冻结的码表（A 在 issue G05-02 里定的），
不用裸 ``HTTPException``：裸异常只能表达状态码，区分不出
"设备编号重复"和"设备下有活跃测点"这两种完全不同的业务冲突。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from g05.api.deps import get_db, get_operator, get_trace_id
from g05.audit.service import AuditService
from g05.models.device import Device
from g05.models.measure_point import MeasurePoint
from g05.schemas.dto import DeviceCreate, DeviceOut, DeviceUpdate, Page
from g05.schemas.errors import ApiError, ErrorCode
from g05.services.device_service import list_devices

router = APIRouter(prefix="/devices", tags=["设备"])


@router.get("", response_model=Page[DeviceOut], summary="分页查询设备")
def query_devices(
    db: Session = Depends(get_db),
    device_type: str | None = Query(None, description="设备类型过滤"),
    status: str | None = Query(None, description="状态过滤"),
    keyword: str | None = Query(None, description="编号/名称模糊搜索"),
    include_deleted: bool = Query(False, description="是否包含已软删的设备"),
    page: int = Query(1, ge=1, description="页码，从 1 开始；越界页返回空列表"),
    size: int = Query(20, ge=1, le=200, description="每页条数，1~200"),
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
        raise ApiError(
            ErrorCode.DEVICE_NOT_FOUND,
            f"设备 {device_id} 不存在",
            detail={"device_id": device_id},
        )
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
    trace_id: str = Depends(get_trace_id),
):
    exists = db.execute(
        select(Device).where(Device.device_code == payload.device_code)
    ).scalar_one_or_none()
    if exists:
        raise ApiError(
            ErrorCode.DUPLICATE_DEVICE,
            f"设备编号 {payload.device_code} 已存在",
            detail={"device_code": payload.device_code},
        )

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
        trace_id=trace_id,
    )
    db.commit()
    return device


@router.patch("/{device_id}", response_model=DeviceOut, summary="更新设备")
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
    trace_id: str = Depends(get_trace_id),
):
    device = db.get(Device, device_id)
    if device is None or device.is_deleted:
        raise ApiError(
            ErrorCode.DEVICE_NOT_FOUND,
            f"设备 {device_id} 不存在",
            detail={"device_id": device_id},
        )

    # exclude_unset：没传的字段不动它。传了 null 的字段要显式拒绝 ——
    # device_name 等列是 NOT NULL，置空会在 flush 时炸成 500，
    # 而"想把名字清空"本身就不是合法业务意图。
    changes = payload.model_dump(exclude_unset=True)
    nulled = sorted(key for key, value in changes.items() if value is None)
    if nulled:
        raise ApiError(
            ErrorCode.INVALID_PARAM,
            f"字段不能置空：{', '.join(nulled)}",
            detail={"fields": nulled},
        )

    old = device.to_dict()
    device._g05_skip_audit = True  # 下方显式写带 reason 的审计  # noqa: SLF001
    for key, value in changes.items():
        setattr(device, key, value)
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
        trace_id=trace_id,
    )
    db.commit()
    return device


@router.delete("/{device_id}", summary="软删除设备")
def delete_device(
    device_id: int,
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
    trace_id: str = Depends(get_trace_id),
):
    device = db.get(Device, device_id)
    if device is None or device.is_deleted:
        raise ApiError(
            ErrorCode.DEVICE_NOT_FOUND,
            f"设备 {device_id} 不存在",
            detail={"device_id": device_id},
        )

    # 有活跃测点时拒绝删除，保护资产完整性
    active_points = db.execute(
        select(func.count())
        .select_from(MeasurePoint)
        .where(MeasurePoint.device_id == device_id, MeasurePoint.is_deleted.is_(False))
    ).scalar_one()
    if active_points:
        raise ApiError(
            ErrorCode.DEVICE_HAS_ACTIVE_POINTS,
            f"设备下仍有 {active_points} 个活跃测点，请先处理测点",
            detail={"device_id": device_id, "active_points": active_points},
        )

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
        trace_id=trace_id,
    )
    db.commit()
    return {"ok": True, "device_id": device_id, "action": "soft_delete"}
