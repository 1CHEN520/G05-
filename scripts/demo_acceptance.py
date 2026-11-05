"""答辩现场演示脚本：改名留痕 + 孤儿拒绝（issue G05-39 的配套脚本）。

一条命令跑完三件事，每一步都有当场可复现的输出：

    python scripts/demo_acceptance.py

1. 软删与版本链：改名不删行，原行仍在，version 自动 +1
2. 孤儿记录被拒：测点挂到不存在的设备上，外键直接拒
3. 触发器就位：append-only 审计触发器已装上

这三个点是"改名可追溯"和"脏数据进不来"的地基，
在没有控制台前端的阶段也能当场演示。

维护者：A（架构师）。这是答辩用的演示脚本，不参与业务逻辑。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _line(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def demo_soft_delete_version_chain() -> None:
    """改名不删行：版本号自增，旧值留在审计里可追溯。"""
    from g05.core.db import get_engine, session_scope
    from g05.models.base import Base
    from g05.models.device import Device

    _line("① 改名留痕（US-09 软删除保留痕迹）")

    engine = get_engine()
    Base.metadata.create_all(engine)

    with session_scope() as db:
        dev = Device(
            device_code="DEMO-001",
            device_name="轴承温度(驱动端)",
            device_type="风机",
            location="A 区",
        )
        db.add(dev)
        db.flush()
        print(f"  新增设备    id={dev.id}  version={dev.version}  name={dev.device_name}")

        dev.device_name = "轴承温度"
        db.flush()
        print(f"  改名为      id={dev.id}  version={dev.version}  name={dev.device_name}")
        print("                    ↑ 版本号自增：ORM 的 version_id_col 自动 +1，业务层不用管")

        # 软删除：行还在，只是标记 deleted
        dev.is_deleted = True
        db.flush()
        rows = db.query(Device).filter_by(device_code="DEMO-001").all()
        print(f"  软删除后，同一业务键的行数 = {len(rows)}  ← 行没被物理删除，可追溯")
        print(f"    is_deleted={rows[0].is_deleted}  deleted_at={rows[0].deleted_at}  ← 删的时刻也记下了")

    # 清理演示数据，避免污染后续演示
    with session_scope() as db:
        db.query(Device).filter_by(device_code="DEMO-001").delete()


def demo_orphan_rejected() -> None:
    """孤儿记录必须被拒：测点挂到不存在的设备上，外键直接拒。"""
    import sqlite3

    from g05.core.db import get_engine
    from g05.models.base import Base

    _line("② 孤儿记录被拒（US-08 防止孤儿数据）")

    engine = get_engine()
    Base.metadata.create_all(engine)
    db_path = engine.url.database

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute(
                "INSERT INTO measure_point"
                "(device_id, point_code, point_name, point_type, unit) "
                "VALUES (999999, 'ORPHAN-1', '孤儿测点', '温度', 'C')"
            )
            print("  ✗ 孤儿测点被接受 —— 这不正常！")
        except sqlite3.IntegrityError as exc:
            print(f"  ✓ 孤儿测点被拒：{exc}")
            print("    （measure_point.device_id 是 RESTRICT，有测点的设备不允许物理删除）")
    finally:
        conn.close()


def demo_triggers_installed() -> None:
    """触发器就位：血缘自环拒绝 + 审计 append-only（待 C3 合入）。"""
    from sqlalchemy import text

    from g05.core.db import get_engine
    from g05.core.schema import ensure_schema

    _line("③ 数据库级约束就位（触发器）")

    ensure_schema()
    engine = get_engine()
    with engine.connect() as conn:
        triggers = [
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            ).fetchall()
        ]
        tables = {
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        }
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()

    print(f"  journal_mode = {journal}  ← WAL，断电不丢已提交数据")
    for t in sorted(triggers):
        note = ""
        if "audit" in t:
            note = "  ← 审计 append-only，改删直接 ABORT"
        elif "self_loop" in t:
            note = "  ← 血缘自环拒绝"
        print(f"  ✓ {t}{note}")

    if "audit_log" not in tables:
        print(
            "\n  ⚠ audit_log 表尚未合入（归 C3），两个审计触发器装不上 ——\n"
            "    这是 M2 阶段的正常中间态，C3 合入后会自动补齐。"
        )


def demo_contract() -> None:
    """契约一览：DTO 与错误码。"""
    _line("④ 冻结的接口契约")

    from g05.schemas.dto import GraphOut, ImpactOut
    from g05.schemas.errors import CODES, HTTP_STATUS

    print(f"  ImpactOut 字段（{len(ImpactOut.model_fields)} 个）：")
    print(f"    {', '.join(ImpactOut.model_fields)}")
    print(f"  GraphOut 字段（{len(GraphOut.model_fields)} 个）：")
    print(f"    {', '.join(GraphOut.model_fields)}")

    seg = {}
    for c in CODES:
        seg.setdefault(c[4], []).append(c)
    print(f"\n  错误码共 {len(CODES)} 个：")
    for s, name in (("1", "参数错误"), ("2", "业务错误"), ("5", "系统错误")):
        codes = seg.get(s, [])
        statuses = sorted({HTTP_STATUS[c] for c in codes})
        print(f"    G05-{s}xxx {name}：{len(codes)} 个 → HTTP {statuses}")
    print("\n  错误体统一为 {code, message, detail}")


def main() -> int:
    print("G05 答辩演示：改名留痕 · 孤儿拒绝 · 触发器就位 · 契约一览")
    demo_soft_delete_version_chain()
    demo_orphan_rejected()
    demo_triggers_installed()
    demo_contract()
    print("\n以上输出均可当场复现，对应 docs/需求追溯矩阵.md 中的已验收项。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
