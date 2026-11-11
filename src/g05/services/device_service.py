"""设备业务服务：分页查询。

从 :mod:`g05.services.point_service` 里独立出来 —— 设备列表与测点列表
是两套不同的过滤维度（类型/状态 vs 测点类型/所属设备），放在一起
只会让两边互相干扰。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from g05.models.device import Device


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
    """分页查询设备，返回 ``(items, total)``。

    **total 与 items 必须用同一组 where 条件** —— 这是分页最常见的 bug：
    count 忘了加 ``is_deleted`` 过滤，就会出现"total=100 但只能翻到 80 条、
    最后一页是空的"。所以这里把条件收进一个 ``conditions`` 列表，
    同时喂给两条语句，不复制粘贴。
    """
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
    # 越界页返回空列表而不是报错：前端翻到第 999 页是正常操作
    stmt = stmt.order_by(Device.id).limit(size).offset((page - 1) * size)
    return list(session.execute(stmt).scalars()), total
