"""测点业务服务：改名（含血缘影响面）+ 软删 + 版本链。

改名不删除原则：
- 物理行保留，``version`` 自增
- 旧名写入 ``renamed_from``，变更原因写入 ``change_note``
- 审计表记录 old/new 快照，可回溯任一版本
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from g05.audit.service import AuditService
from g05.core.logging import get_logger, log_event
from g05.lineage.impact import impact_of
from g05.models.device import Device
from g05.models.measure_point import MeasurePoint

log = get_logger("services.point")


class PointNotFoundError(LookupError):
    pass


class PointConflictError(RuntimeError):
    pass


def get_point(session: Session, point_id: int) -> MeasurePoint:
    point = session.get(MeasurePoint, point_id)
    if point is None or point.is_deleted:
        raise PointNotFoundError(f"测点 {point_id} 不存在或已删除")
    return point


def rename_point(
    session: Session,
    point_id: int,
    new_name: str,
    *,
    operator: str = "system",
    reason: str | None = None,
    include_impact: bool = True,
) -> dict:
    """重命名测点，返回影响面。

    影响面查询在改名**前**执行（基于 point_code，不受名称变化影响），
    这样即使下游按名称匹配也能拿到改名前的依赖关系。
    """
    point = get_point(session, point_id)

    if point.point_name == new_name:
        raise PointConflictError(f"新名称与当前名称相同: {new_name}")

    # 1) 先算影响面（改名前后 point_code 不变，结果一致，但语义上属于"改名前评估"）
    impact_payload: dict = {}
    if include_impact:
        result = impact_of(
            session, node_type="point", node_key=point.point_code, direction="downstream"
        )
        impact_payload = result.to_dict()

    # 2) 快照 + 更新
    old_snapshot = {"point_name": point.point_name, "version": point.version}
    old_name = point.point_name

    point.renamed_from = old_name
    point.point_name = new_name
    point.version = point.version + 1
    point.change_note = reason
    point.updated_at = datetime.now(timezone.utc)
    # 告诉自动审计：本对象已由下方显式记录 RENAME，勿重复写 UPDATE
    point._g05_skip_audit = True  # noqa: SLF001

    # 3) 审计（显式写 RENAME，带原因）
    AuditService.record(
        session,
        table_name="measure_point",
        action="RENAME",
        operator=operator,
        record_id=point.id,
        business_key=point.point_code,
        old=old_snapshot,
        new={"point_name": new_name, "version": point.version},
        reason=reason or "测点改名",
    )

    session.commit()

    log_event(
        log,
        "point.renamed",
        point_id=point.id,
        point_code=point.point_code,
        old=old_name,
        new=new_name,
        affected=impact_payload.get("total", 0),
    )

    return {
        "point_id": point.id,
        "old_name": old_name,
        "new_name": new_name,
        "new_version": point.version,
        "superseded_by": point.id,
        "affected": impact_payload,
    }


def soft_delete_point(
    session: Session, point_id: int, *, operator: str = "system", reason: str | None = None
) -> MeasurePoint:
    """软删测点：不物理删除，保留审计痕迹。"""
    point = get_point(session, point_id)
    old = point.to_dict()

    point.is_deleted = True
    point.deleted_at = datetime.now(timezone.utc)
    point.version += 1
    point.change_note = reason
    point._g05_skip_audit = True  # 下方显式写 SOFT_DELETE 审计  # noqa: SLF001

    AuditService.record(
        session,
        table_name="measure_point",
        action="SOFT_DELETE",
        operator=operator,
        record_id=point.id,
        business_key=point.point_code,
        old={"is_deleted": old["is_deleted"], "version": old["version"]},
        new={"is_deleted": True, "version": point.version},
        reason=reason or "软删除测点",
    )
    session.commit()
    log_event(log, "point.soft_deleted", point_id=point.id, operator=operator)
    return point


def list_points(
    session: Session,
    *,
    device_id: int | None = None,
    point_type: str | None = None,
    keyword: str | None = None,
    include_deleted: bool = False,
    page: int = 1,
    size: int = 20,
) -> tuple[list[MeasurePoint], int]:
    from sqlalchemy import func

    stmt = select(MeasurePoint)
    count_stmt = select(func.count()).select_from(MeasurePoint)

    conditions = []
    if not include_deleted:
        conditions.append(MeasurePoint.is_deleted.is_(False))
    if device_id:
        conditions.append(MeasurePoint.device_id == device_id)
    if point_type:
        conditions.append(MeasurePoint.point_type == point_type)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            MeasurePoint.point_name.like(like) | MeasurePoint.point_code.like(like)
        )

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = session.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(MeasurePoint.id).limit(size).offset((page - 1) * size)
    return list(session.execute(stmt).scalars()), total


def list_devices(
    session: Session,
    *,
    device_type: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    include_deleted: bool = False,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Device], int]:
    from sqlalchemy import func

    stmt = select(Device)
    count_stmt = select(func.count()).select_from(Device)

    conditions = []
    if not include_deleted:
        conditions.append(Device.is_deleted.is_(False))
    if device_type:
        conditions.append(Device.device_type == device_type)
    if status:
        conditions.append(Device.status == status)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(Device.device_name.like(like) | Device.device_code.like(like))

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = session.execute(count_stmt).scalar_one()
    stmt = stmt.order_by(Device.id).limit(size).offset((page - 1) * size)
    return list(session.execute(stmt).scalars()), total
