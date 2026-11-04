"""初始化数据库：建表 + 审计防篡改触发器。

用法::

    python scripts/init_db.py            # 建表（已存在则跳过，幂等）
    python scripts/init_db.py --drop     # 先删后建（危险，仅开发用）

团队统一入口是 ``python -m g05 initdb [--drop]``（A 维护的 ``g05.cli``），
本脚本是等价的独立入口，方便在没有装包的目录下直接跑。

**幂等性**：重复执行不报错。``create_all`` 只补建缺失的表，
触发器用 ``CREATE TRIGGER IF NOT EXISTS``。

**触发器不自己写**：统一调用 :func:`g05.core.schema.apply_triggers`（A 维护），
避免两处定义漂移 —— 这正是分工文件清单里"触发器 DDL 归 A"的约定。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本能直接 python scripts/xxx.py 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from g05.core.db import get_engine, healthcheck  # noqa: E402
from g05.core.logging import get_logger, log_event  # noqa: E402
from g05.core.schema import TRIGGERS, apply_triggers  # noqa: E402
from g05.models import Base  # noqa: E402

log = get_logger("scripts.init_db")


def create_tables(drop: bool = False) -> list[str]:
    """建表并应用触发器，返回表名清单。"""
    engine = get_engine()

    if drop:
        log.warning("dropping all tables (destructive)", extra={"extra_fields": {"drop": True}})
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)

    table_names = sorted(Base.metadata.tables.keys())
    log_event(log, "tables.created", tables=table_names)

    with engine.begin() as conn:
        apply_triggers(conn)
    log_event(log, "triggers.created", count=len(TRIGGERS))

    return table_names


def list_tables() -> list[str]:
    """查询数据库中实际存在的表。"""
    from sqlalchemy import text

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ).fetchall()
    return sorted(row[0] for row in rows)


def list_triggers() -> list[str]:
    """查询数据库中实际存在的触发器。"""
    from sqlalchemy import text

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
        ).fetchall()
    return [row[0] for row in rows]


def verify() -> dict[str, object]:
    """自检：外键必须开、WAL 必须开，并打印表/触发器清单。"""
    info = healthcheck()
    log_event(log, "db.healthcheck", **info)

    if not info["foreign_keys"]:
        raise RuntimeError("外键约束未开启，孤儿记录将无法被拦截")
    if str(info["journal_mode"]).lower() != "wal":
        raise RuntimeError("WAL 模式未启用，崩溃恢复能力不达标")

    tables = list_tables()
    triggers = list_triggers()

    print("\n[OK] 数据库自检通过")
    for key, value in info.items():
        print(f"  {key:16s} = {value}")

    print(f"\n[表清单] 共 {len(tables)} 张表")
    for name in tables:
        print(f"  - {name}")

    print(f"\n[触发器清单] 共 {len(triggers)} 个")
    for name in triggers:
        print(f"  - {name}")

    return {"health": info, "tables": tables, "triggers": triggers}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="初始化 G05 数据库")
    parser.add_argument("--drop", action="store_true", help="先删除所有表再重建（危险）")
    args = parser.parse_args(argv)

    create_tables(drop=args.drop)
    verify()


if __name__ == "__main__":
    main()
