"""模型统一导出。

这个模块是**公共契约**：

- ``Base.metadata.create_all()`` 依赖这里把全部模型导入进来，
  少导一个就少建一张表（验收要求 7 张表）
- 下游 C1/C2/C3/C4 都 ``from g05.models import Xxx``，导出名改动会炸掉别人

因此：新增模型请在这里加一行导出，**不要改名、不要删除已有导出**。

``audit_log`` 归 C3（审计与变更追踪）。C3 的分支尚未合入时，
这里对它做降级处理，保证 B1 的模型层能独立建库、跑测试。
"""

from g05.models.alarm import Alarm
from g05.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UTCDateTime,
    alive_filter,
    utcnow,
)
from g05.models.device import Device
from g05.models.lineage import LineageEdge, LineageNode
from g05.models.measure_point import MeasurePoint
from g05.models.record import Record

try:  # C3 的审计表模型：未合入时不影响其余 6 张表建库
    from g05.models.audit_log import AuditLog
except ModuleNotFoundError as _exc:  # pragma: no cover - 取决于 C3 进度
    if _exc.name != "g05.models.audit_log":
        raise
    AuditLog = None  # type: ignore[assignment,misc]

__all__ = [
    "Alarm",
    "AuditLog",
    "Base",
    "Device",
    "LineageEdge",
    "LineageNode",
    "MeasurePoint",
    "Record",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UTCDateTime",
    "alive_filter",
    "utcnow",
]
