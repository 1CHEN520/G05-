"""core/schema.py 触发器装载与自检（issue #63）。

守护测试的核心纪律：必须验证「自检真的能抓到」，而不是只测正常路径。
模拟 DDL 静默失效的方式：拦截 ``sqlalchemy.text``，把 CREATE TRIGGER
语句偷换成无害的 ``SELECT 1``——执行不报错、触发器不落地，
``apply_triggers`` 的自检必须据此 fail-fast。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as real_text

from g05.core.schema import apply_triggers
from g05.models import Base

_ALL_TRIGGER_NAMES = {
    "trg_audit_no_update",
    "trg_audit_no_delete",
    "trg_edge_no_self_loop",
}


def _trigger_names(conn) -> set[str]:  # noqa: ANN001
    rows = conn.execute(real_text("SELECT name FROM sqlite_master WHERE type='trigger'")).fetchall()
    return {row[0] for row in rows}


class TestTriggerSelfCheck:
    """apply_triggers 的自检行为（issue #63）。"""

    def test_all_triggers_present_after_apply(self, db_path):
        """正向：全表建齐后 apply，3 个触发器必须全部落地。"""
        from g05.core.db import get_engine

        engine = get_engine()
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            apply_triggers(conn)

        with engine.connect() as conn:
            assert _trigger_names(conn) >= _ALL_TRIGGER_NAMES

    def test_apply_raises_when_ddl_silently_lost(self, db_path, monkeypatch):
        """守护：DDL 静默失效（执行成功但触发器没落地）必须抛错，禁止带病启动。

        这是 C3 在 PR #57 中指出的真实威胁：IF NOT EXISTS 的容错会掩盖
        装载失败；空表上 UPDATE 匹配 0 行不会触发 BEFORE UPDATE 触发器，
        据此判定"防篡改正常"是误报合规问题。自检抓不到等于没做。
        """
        import sqlalchemy

        def fake_text(sql):
            """把 CREATE TRIGGER 偷换成 SELECT 1，模拟 DDL 静默失效。"""
            if "CREATE TRIGGER" in sql:
                return real_text("SELECT 1")
            return real_text(sql)

        monkeypatch.setattr(sqlalchemy, "text", fake_text)

        from g05.core.db import get_engine

        engine = get_engine()
        Base.metadata.create_all(engine)
        with pytest.raises(RuntimeError, match="trigger self-check failed"), engine.begin() as conn:
            apply_triggers(conn)

    def test_missing_table_skipped_without_error(self, db_path):
        """骨架兼容：目标表不存在时跳过对应触发器，自检不得误报。"""
        from g05.core.db import get_engine

        engine = get_engine()
        # 只建 lineage_edge 表（audit_log 不建）→ audit 触发器应被跳过
        Base.metadata.create_all(engine, tables=[Base.metadata.tables["lineage_edge"]])
        with engine.begin() as conn:
            apply_triggers(conn)  # 不应抛错

        with engine.connect() as conn:
            names = _trigger_names(conn)
        assert "trg_edge_no_self_loop" in names
        assert not ({"trg_audit_no_update", "trg_audit_no_delete"} & names)
