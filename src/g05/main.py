"""G05 应用入口。

一键运行::

    python -m g05.main                 # 或 uvicorn g05.main:app --reload
    浏览器打开 http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import importlib
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from g05.core.config import PROJECT_ROOT, settings
from g05.core.db import get_engine, healthcheck
from g05.core.logging import get_logger, log_event

try:  # 骨架阶段 models 可能还没合入（B1 负责）
    from g05.models import Base
except ModuleNotFoundError:  # pragma: no cover - 只在骨架阶段触发
    Base = None  # type: ignore[assignment]

log = get_logger("main")

# 路由模块按角色分工落地，允许"还没合进来"：
# 骨架阶段某个模块缺失时只告警、不崩，这样组长推完骨架就能起服务，
# 各成员的分支陆续合并后路由自然补齐。
ROUTE_MODULES = ["devices", "points", "lineage", "audit", "imports"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：确保表存在（幂等），安装自动审计
    from g05.core.schema import apply_triggers

    engine = get_engine()
    if Base is not None:
        Base.metadata.create_all(engine)
    else:  # pragma: no cover - 只在骨架阶段触发
        log.warning("models.missing", extra={"extra_fields": {"hint": "B1 的模型尚未合入"}})
    with engine.begin() as conn:
        apply_triggers(conn)

    try:
        from g05.audit.service import install_auto_audit

        install_auto_audit()
    except ModuleNotFoundError:  # pragma: no cover - 只在骨架阶段触发
        log.warning("audit.missing", extra={"extra_fields": {"hint": "C3 的审计模块尚未合入"}})

    info = healthcheck()
    log_event(log, "app.startup", **info)
    if not info["ok"]:
        log.error("数据库自检未通过：请检查外键与 WAL 设置")

    yield

    log.info("app.shutdown")


app = FastAPI(
    title=settings.get("app.app.name", "G05 设备台账与测点资产目录"),
    version=settings.get("app.app.version", "0.1.0"),
    description=(
        "工业级 L2 · 设备/测点/记录/告警四表 + 血缘影响面 + 审计可回溯\n\n"
        "**开题四问**：断电重启不丢数（WAL）/ 并发可控（连接池+bulk）/ "
        "变更可追溯（append-only 审计）/ 换机可跑（配置外置+一键启动）"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """请求耗时埋点，超过 1s 打 WARN。"""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Elapsed-Ms"] = f"{elapsed_ms:.1f}"
    if elapsed_ms > 1000:
        log.warning(
            "slow request",
            extra={
                "extra_fields": {
                    "path": request.url.path,
                    "method": request.method,
                    "elapsed_ms": round(elapsed_ms, 1),
                }
            },
        )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底异常：记录完整堆栈，对外只返回简要信息（不泄露内部细节）。"""
    log.exception(
        "unhandled error",
        extra={"extra_fields": {"path": request.url.path, "error": str(exc)}},
    )
    return JSONResponse(
        status_code=500,
        content={"ok": False, "message": "服务内部错误，请查看服务端日志", "path": request.url.path},
    )


# ---------------- 路由注册 ----------------
API_PREFIX = "/api"
for _name in ROUTE_MODULES:
    try:
        _module = importlib.import_module(f"g05.api.routes.{_name}")
    except ModuleNotFoundError as _exc:
        log.warning("route.module.missing", extra={"extra_fields": {"module": _name, "err": str(_exc)}})
        continue
    app.include_router(_module.router, prefix=API_PREFIX)


@app.get("/healthz", tags=["运维"], summary="健康检查（含真实 DB 探测）")
def healthz():
    info = healthcheck()
    return {
        "ok": info["ok"],
        "database": info,
        "app": {
            "name": settings.get("app.app.name"),
            "version": settings.get("app.app.version"),
            "env": settings.get("app.app.env"),
        },
    }


@app.get("/metrics", tags=["运维"], summary="轻量运行指标")
def metrics():
    from sqlalchemy import func, select

    from g05.core.db import session_scope
    from g05.models.audit_log import AuditLog
    from g05.models.device import Device
    from g05.models.measure_point import MeasurePoint

    with session_scope() as db:
        return {
            "devices": db.execute(select(func.count()).select_from(Device)).scalar_one(),
            "points": db.execute(select(func.count()).select_from(MeasurePoint)).scalar_one(),
            "audit_entries": db.execute(select(func.count()).select_from(AuditLog)).scalar_one(),
        }


# ---------------- 静态首页 ----------------
_WEB_DIR = PROJECT_ROOT / "web"
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    page = _WEB_DIR / "index.html"
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>G05 设备台账与测点资产目录</h1>"
        "<p>接口文档：<a href='/docs'>/docs</a> ｜ 健康检查：<a href='/healthz'>/healthz</a></p>"
    )


def main() -> None:
    import uvicorn

    host = settings.get("app.server.host", "127.0.0.1")
    port = int(settings.get("app.server.port", 8000))
    print(f"\n  G05 服务启动中 -> http://{host}:{port}/docs\n")
    uvicorn.run("g05.main:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
