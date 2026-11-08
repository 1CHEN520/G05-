"""FastAPI 依赖：会话、操作人、trace_id。

操作人从请求头 ``X-Operator`` 读取，缺省 ``anonymous``；
trace_id 从 ``X-Trace-Id`` 读取，缺省自动生成，贯穿审计与日志。

trace_id 的取值优先级：
1. ``request.state.trace_id`` —— 由 main.py 的 trace 中间件写入，
   与全局异常处理器用的是同一个号，所以"响应里的号"和"日志里的号"必然对得上；
2. 请求头 ``X-Trace-Id`` —— 允许调用方（如前端）自带链路号；
3. 新生成的 uuid。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Header, Request
from sqlalchemy.orm import Session

from g05.core.db import session_scope


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def get_operator(x_operator: str | None = Header(default=None)) -> str:
    return (x_operator or "anonymous").strip()[:64]


def get_trace_id(
    request: Request,
    x_trace_id: str | None = Header(default=None),
) -> str:
    """本次请求的链路号。

    优先复用中间件写入的 ``request.state.trace_id``：
    同一请求内被依赖注入调用多次也必须是同一个号，
    否则审计记录里的 trace_id 和错误响应里的 trace_id 会对不上。
    """
    state_trace_id = getattr(request.state, "trace_id", None)
    if state_trace_id:
        return str(state_trace_id)
    return (x_trace_id or uuid.uuid4().hex)[:64]
