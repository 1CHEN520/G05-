"""FastAPI 依赖：会话、操作人、trace_id。

操作人从请求头 ``X-Operator`` 读取，缺省 ``anonymous``；
trace_id 从 ``X-Trace-Id`` 读取，缺省自动生成，贯穿审计与日志。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Header
from sqlalchemy.orm import Session

from g05.core.db import session_scope


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def get_operator(x_operator: str | None = Header(default=None)) -> str:
    return (x_operator or "anonymous").strip()[:64]


def get_trace_id(x_trace_id: str | None = Header(default=None)) -> str:
    return (x_trace_id or uuid.uuid4().hex)[:64]
