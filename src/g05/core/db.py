"""数据库引擎与会话管理（SQLAlchemy 2.0 + SQLite WAL）。

关键点：
1. SQLite 默认关闭外键约束，必须在每个连接上显式 ``PRAGMA foreign_keys=ON``
2. WAL 模式 + synchronous=NORMAL：kill -9 后重启数据不丢
3. busy_timeout 防止并发写时 "database is locked"
4. 慢查询埋点：执行超过阈值的 SQL 打 WARN 日志
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from g05.core.config import settings
from g05.core.logging import get_logger

log = get_logger("core.db")

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _build_url() -> str:
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.as_posix()}"


def _apply_pragmas(dbapi_conn, _record) -> None:
    """每个新建的 DBAPI 连接都执行一次 PRAGMA。"""
    pragmas = settings.get("database.database.pragma", {}) or {}
    cursor = dbapi_conn.cursor()
    try:
        for key, value in pragmas.items():
            cursor.execute(f"PRAGMA {key}={value}")
    finally:
        cursor.close()


def _install_slow_query_hook(engine: Engine, threshold_ms: int) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        context._g05_t0 = time.perf_counter()  # noqa: SLF001

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        started = getattr(context, "_g05_t0", None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms >= threshold_ms:
            log.warning(
                "slow query",
                extra={
                    "extra_fields": {
                        "elapsed_ms": round(elapsed_ms, 1),
                        "sql": statement[:300].replace("\n", " "),
                    }
                },
            )


def get_engine() -> Engine:
    """进程级单例 Engine。"""
    global _engine, _SessionFactory
    if _engine is not None:
        return _engine

    pool = settings.get("database.database.pool", {}) or {}
    _engine = create_engine(
        _build_url(),
        echo=False,
        future=True,
        pool_size=pool.get("size", 10),
        max_overflow=pool.get("max_overflow", 20),
        pool_timeout=pool.get("pool_timeout", 30),
        pool_recycle=pool.get("pool_recycle", 1800),
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    event.listen(_engine, "connect", _apply_pragmas)
    _install_slow_query_hook(_engine, settings.get("database.database.slow_query_ms", 200))

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    log.info(
        "engine created",
        extra={"extra_fields": {"url": _build_url(), "pool_size": pool.get("size", 10)}},
    )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务上下文：正常提交，异常回滚，最终关闭。"""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""
    with session_scope() as session:
        yield session


def reset_engine() -> None:
    """测试用：释放并重建 Engine。"""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


def healthcheck() -> dict:
    """自检：能连通、外键已开、WAL 已启用。"""
    engine = get_engine()
    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        version = conn.execute(text("SELECT sqlite_version()")).scalar()
    return {
        "ok": bool(fk) and str(journal).lower() == "wal",
        "foreign_keys": bool(fk),
        "journal_mode": journal,
        "sqlite_version": version,
        "db_path": str(settings.db_path),
    }
