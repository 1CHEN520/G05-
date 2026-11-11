"""业务服务层。

对外只暴露两个模块的公共入口，路由层不要直接 import 具体函数，
统一从 :mod:`g05.services` 取。
"""

from __future__ import annotations

from g05.services.device_service import list_devices
from g05.services.point_service import (
    PointConflictError,
    PointNotFoundError,
    list_points,
    rename_point,
    soft_delete_point,
)

__all__ = [
    "PointConflictError",
    "PointNotFoundError",
    "list_devices",
    "list_points",
    "rename_point",
    "soft_delete_point",
]
