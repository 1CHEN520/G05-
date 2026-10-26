"""初始化数据库：建表 + 审计防篡改触发器。

用法::

    python scripts/init_db.py            # 建表（已存在则跳过）
    python scripts/init_db.py --drop     # 先删后建（危险，仅开发用）
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


def create_tables(drop: bool = False) -> None:
    engine = get_engine()

    if drop:
        log.warning("dropping all tables (destructive)")
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)
    log_event(log, "tables.created", tables=sorted(Base.metadata.tables.keys()))

    with engine.begin() as conn:
        apply_triggers(conn)
    log_event(log, "triggers.created", count=len(TRIGGERS))


def verify() -> None:
    info = healthcheck()
    log_event(log, "db.healthcheck", **info)
    if not info["foreign_keys"]:
        raise RuntimeError("外键约束未开启，孤儿记录将无法被拦截")
    if str(info["journal_mode"]).lower() != "wal":
        raise RuntimeError("WAL 模式未启用，崩溃恢复能力不达标")
    print("\n[OK] 数据库自检通过")
    for key, value in info.items():
        print(f"  {key:16s} = {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 G05 数据库")
    parser.add_argument("--drop", action="store_true", help="先删除所有表再重建（危险）")
    args = parser.parse_args()

    create_tables(drop=args.drop)
    verify()


if __name__ == "__main__":
    main()
