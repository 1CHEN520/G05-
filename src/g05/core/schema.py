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
    """在给定连接/事务上创建全部触发器（幂等），并**自检**安装结果。

    只对**已存在的表**装触发器：骨架阶段某些模块还没合进来（表不存在）时跳过，
    而不是让整个应用起不来。

    装完后验证"本应安装"的触发器确实出现在 ``sqlite_master``，
    缺了立即抛错——绝不带病启动（issue #63）。

    为什么需要自检：``CREATE TRIGGER IF NOT EXISTS`` 在目标表存在但 DDL
    因任何原因未生效时会**静默跳过**。而空表上 ``UPDATE ... WHERE id=(SELECT
    MIN(id))`` 匹配 0 行，SQLite 不会触发 ``BEFORE UPDATE`` 触发器、语句
    成功返回——据此判定"防篡改正常"是误报合规问题（C3 在 PR #57 中发现）。
    """
    import re

    from sqlalchemy import text

    tables = {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }
    expected: list[str] = []
    for ddl in TRIGGERS:
        m = re.search(r"\bON\s+([A-Za-z_][A-Za-z0-9_]*)", ddl)
        if m and m.group(1) not in tables:
            continue
        conn.execute(text(ddl))
        tm = re.search(r"CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", ddl)
        if tm:
            expected.append(tm.group(1))

    # 自检：本应装上的触发器必须真的在 sqlite_master 里
    if expected:
        present = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).fetchall()
        }
        missing = [name for name in expected if name not in present]
        if missing:
            raise RuntimeError(
                f"trigger self-check failed: missing triggers {missing}. "
                "audit append-only 保障不完整，禁止带病启动（issue #63）"
            )


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
