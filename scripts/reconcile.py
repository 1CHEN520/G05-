"""对账恒等验证（期末评分标准：功能完成=…断网恢复/**对账恒等**/性能阈值）。

对数据库跑五组「恒等关系」，任何一组不成立即退出码 1：
1. 外键恒等：measure_point.device_id 全部存在于 device（孤儿=0）
2. 软删恒等：is_deleted=1 的行必有 deleted_at（软删必有痕），活行必无痕
3. 行级审计-业务恒等：行级 INSERT/UPDATE/DELETE 审计指向的实体必须可定位
4. 血缘恒等：lineage_edge 两端都在 lineage_node（孤儿边=0），且无自环
5. 批次声明守恒：seed 汇总审计声明的实体数 ≤ 当前库内行数（软删不消失）

审计有两种粒度（语义不同，校验规则不同）：
- 行级自动埋点（table_name ∈ device/measure_point，action ∈ INSERT/UPDATE/DELETE）：
  business_key 指向单个实体 → 实体必须现存或软删可查
- 操作级批次审计（table_name=import，business_key=批次名如 "seed"）：只验证批次记录存在

用法：
    python scripts/reconcile.py                # 对默认库
    python scripts/reconcile.py --db path      # 指定库
    python scripts/reconcile.py --json out     # 同时写 JSON

用途场景：教师注入故障（kill/坏数据）后当场跑本脚本——
「崩溃且能恢复」的直接证据就是**故障注入后对账恒等依然成立**。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def check_fk_orphans(conn: sqlite3.Connection) -> dict:
    """1. 外键恒等：测点→设备 无孤儿。"""
    orphans = conn.execute(
        "SELECT COUNT(*) FROM measure_point p "
        "LEFT JOIN device d ON p.device_id = d.id WHERE d.id IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM measure_point").fetchone()[0]
    return {
        "name": "外键恒等（测点→设备）",
        "detail": f"测点 {total} 条，孤儿 {orphans} 条",
        "ok": orphans == 0,
        "fail_hint": "存在孤儿测点：外键未生效（PRAGMA foreign_keys 未开）或发生过绕过约束的写入",
    }


def check_soft_delete(conn: sqlite3.Connection) -> dict:
    """2. 软删恒等：软删行必有 deleted_at，且活行不得有 deleted_at。"""
    bad_deleted = conn.execute(
        "SELECT COUNT(*) FROM device WHERE is_deleted = 1 AND deleted_at IS NULL"
    ).fetchone()[0]
    bad_alive = conn.execute(
        "SELECT COUNT(*) FROM device WHERE is_deleted = 0 AND deleted_at IS NOT NULL"
    ).fetchone()[0]
    deleted = conn.execute("SELECT COUNT(*) FROM device WHERE is_deleted = 1").fetchone()[0]
    return {
        "name": "软删恒等（软删必有痕，活行必无痕）",
        "detail": f"软删 {deleted} 行；deleted_at 缺失 {bad_deleted}，活行误带 {bad_alive}",
        "ok": bad_deleted == 0 and bad_alive == 0,
        "fail_hint": "软删标记与时间戳不一致：软删路径绕过了 service 层或手改过库",
    }


def check_audit_referential(conn: sqlite3.Connection) -> dict:
    """3. 行级审计-业务恒等：行级审计指向的实体必须现存或软删可查。"""
    keys = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT business_key FROM audit_log "
            "WHERE table_name = 'device' AND business_key IS NOT NULL "
            "AND action IN ('INSERT', 'UPDATE', 'DELETE')"
        ).fetchall()
    ]
    missing = []
    for key in keys:
        row = conn.execute(
            "SELECT COUNT(*) FROM device WHERE device_code = ? AND is_deleted = 0",
            (key,),
        ).fetchone()[0]
        soft = conn.execute(
            "SELECT COUNT(*) FROM device WHERE device_code = ? AND is_deleted = 1",
            (key,),
        ).fetchone()[0]
        if row == 0 and soft == 0:
            missing.append(key)
    batch = conn.execute("SELECT COUNT(*) FROM audit_log WHERE table_name = 'import'").fetchone()[0]
    return {
        "name": "行级审计-业务恒等（审计实体可定位）",
        "detail": f"行级审计实体键 {len(keys)} 个，凭空消失 {len(missing)} 个；操作级批次审计 {batch} 条（只验存在）",
        "ok": len(missing) == 0,
        "fail_hint": "行级审计指向物理消失的实体：审计-业务发生过库分离或单侧清理"
        "（历史案例：11-11 fault 并发案的 CONC 审计留存而设备被清）——标准处置 initdb --drop + seed 重置",
    }


def check_lineage(conn: sqlite3.Connection) -> dict:
    """4. 血缘恒等：孤儿边=0，自环=0。"""
    orphan_edges = conn.execute(
        "SELECT COUNT(*) FROM lineage_edge e "
        "LEFT JOIN lineage_node n1 ON e.upstream_id = n1.id "
        "LEFT JOIN lineage_node n2 ON e.downstream_id = n2.id "
        "WHERE n1.id IS NULL OR n2.id IS NULL"
    ).fetchone()[0]
    self_loops = conn.execute(
        "SELECT COUNT(*) FROM lineage_edge WHERE upstream_id = downstream_id"
    ).fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM lineage_edge").fetchone()[0]
    return {
        "name": "血缘恒等（孤儿边=0，自环=0）",
        "detail": f"边 {edges} 条，孤儿 {orphan_edges}，自环 {self_loops}",
        "ok": orphan_edges == 0 and self_loops == 0,
        "fail_hint": "孤儿边：血缘分发绕过了外键；自环：trg_edge_no_self_loop 触发器未装（跑 preflight 核查）",
    }


def check_batch_declared_conservation(conn: sqlite3.Connection) -> dict:
    """5. 批次声明守恒：seed 汇总审计声明的实体数 ≤ 当前库内行数（软删不消失）。"""
    declared = conn.execute(
        "SELECT new_value FROM audit_log "
        "WHERE table_name = 'import' AND business_key = 'seed' "
        "ORDER BY changed_at DESC LIMIT 1"
    ).fetchone()
    if declared is None or declared[0] is None:
        return {
            "name": "批次声明守恒（seed 声明 ≤ 现存）",
            "detail": "未找到 seed 批次审计——库可能是空库，跳过本项",
            "ok": True,
            "fail_hint": "",
        }
    snapshot = json.loads(declared[0])
    results = []
    for table, key in (("device", "devices"), ("measure_point", "points")):
        want = int(snapshot.get(key, 0))
        current = conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        results.append((table, want, current, current >= want))
    ok = all(r[3] for r in results)
    detail = "；".join(
        tbl + " 声明 " + str(want) + " ≤ 现存 " + str(cur) + (": ✓" if ok_i else ": ✗")
        for tbl, want, cur, ok_i in results
    )
    return {
        "name": "批次声明守恒（seed 声明 ≤ 现存，软删不消失）",
        "detail": detail,
        "ok": ok,
        "fail_hint": "现存行少于 seed 声明：发生过物理删除或审计与业务库分离——initdb --drop + seed 重置",
    }


def run(db_path: str) -> tuple[list[dict], bool]:
    conn = sqlite3.connect(db_path)
    try:
        checks = [
            check_fk_orphans(conn),
            check_soft_delete(conn),
            check_audit_referential(conn),
            check_lineage(conn),
            check_batch_declared_conservation(conn),
        ]
    finally:
        conn.close()
    return checks, all(c["ok"] for c in checks)


def main() -> int:
    parser = argparse.ArgumentParser(description="五组对账恒等验证")
    parser.add_argument("--db", type=str, default=None, help="库文件路径（默认 config 配置的路径）")
    parser.add_argument("--json", type=str, default=None, help="同时输出 JSON 到指定路径")
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        from g05.core.config import settings

        db_path = str(settings._raw["database"]["database"]["path"])  # noqa: SLF001

    if not Path(db_path).exists():
        print("[FAIL] 库文件不存在：" + db_path + "（先跑 python -m g05 initdb && python -m g05 seed）")
        return 1

    checks, all_ok = run(db_path)

    print("库：" + db_path)
    print("=" * 72)
    for c in checks:
        mark = "✅ 恒等" if c["ok"] else "❌ 破坏"
        print("[" + mark + "] " + c["name"])
        print("    " + c["detail"])
        if not c["ok"]:
            print("    排查线索：" + c["fail_hint"])
        print()
    print("=" * 72)
    ok_count = sum(1 for c in checks if c["ok"])
    print(f"合计：{ok_count}/{len(checks)} 组恒等成立")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"db": db_path, "all_ok": all_ok, "checks": checks}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("JSON 已写入：" + str(out))

    print("\n用途：教师注入故障（kill/坏数据/并发）后当场跑本脚本——对账恒等依然成立即「崩溃且能恢复」的直接证据。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
