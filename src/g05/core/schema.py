"""数据库级约束 DDL（触发器）。

集中定义，供 ``scripts/init_db.py``、应用启动、测试夹具共同引用，
避免"生产有触发器、测试没有"导致的行为不一致。
"""

from __future__ import annotations

# 审计表 append-only：任何 UPDATE / DELETE 直接 ABORT
# 这是"审计不可篡改"的数据库级保障，绕过应用层也改不了
TRIGGERS: list[str] = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE forbidden');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit_log is append-only: DELETE forbidden');
    END;
    """,
    # 血缘边禁止自环（应用层另有 BFS 环检测，这里兜底）
    """
    CREATE TRIGGER IF NOT EXISTS trg_edge_no_self_loop
    BEFORE INSERT ON lineage_edge
    WHEN NEW.upstream_id = NEW.downstream_id
    BEGIN
        SELECT RAISE(ABORT, 'lineage_edge self-loop is forbidden');
    END;
    """,
]


def apply_triggers(conn) -> None:  # noqa: ANN001
    """在给定连接/事务上创建全部触发器（幂等）。

    只对**已存在的表**装触发器：骨架阶段某些模块还没合进来（表不存在）时跳过，
    而不是让整个应用起不来。
    """
    import re

    from sqlalchemy import text

    tables = {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }
    for ddl in TRIGGERS:
        m = re.search(r"\bON\s+([A-Za-z_][A-Za-z0-9_]*)", ddl)
        if m and m.group(1) not in tables:
            continue
        conn.execute(text(ddl))


def ensure_schema(drop: bool = False) -> None:
    """建表 + 建触发器。``drop=True`` 时先清空全部表（本地重置用）。"""
    from g05.core.db import get_engine
    from g05.models import Base

    engine = get_engine()
    if drop:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_triggers(conn)
