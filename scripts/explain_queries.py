"""EXPLAIN QUERY PLAN 实测：验证核心 SQL 的索引命中（期末评分标准：技术深度=压测/p95/EXPLAIN）。

对 G05 三条核心查询路径跑 SQLite 的 EXPLAIN QUERY PLAN，判定每条是否命中索引：
- 命中：计划里出现 ``SEARCH ... USING <index>``（或 UNIQUE 索引）
- 未命中：出现 ``SCAN <table>``（全表扫描）——发现即报告，作为索引补齐的依据

用法：
    python scripts/explain_queries.py            # 对默认库跑，输出表
    python scripts/explain_queries.py --db path  # 指定库文件
    python scripts/explain_queries.py --json out # 同时写 JSON

退出码：全部命中 0；存在 SCAN 1（CI 可挂）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 核心查询路径： label → SQL（参数用 ? 占位，params 提供真实值）
# SQL 与 src/g05 里真实执行的语句保持同构（审计排序/血缘 BFS 边/分页/唯一键定位）。
QUERIES: list[dict] = [
    {
        "label": "审计时间线（按时间倒序分页）",
        "sql": (
            "SELECT id, action, table_name, business_key, changed_at FROM audit_log "
            "ORDER BY changed_at DESC, id DESC LIMIT 200 OFFSET 0"
        ),
        "params": [],
        "expect_index": "ix_audit_table_time",
        "why": "`GET /api/audit` 的主查询（audit/service.py::query）",
    },
    {
        "label": "审计按业务键过滤（打回单/追溯）",
        "sql": (
            "SELECT id, action, changed_at FROM audit_log "
            "WHERE business_key = ? ORDER BY changed_at DESC"
        ),
        "params": ["FAN-0001"],
        "expect_index": "ix_audit_business_time",
        "why": "按业务键追溯变更历史（G05-21 查询路径）",
    },
    {
        "label": "血缘 BFS 一层展开（按上游找下游）",
        "sql": (
            "SELECT upstream_id, downstream_id FROM lineage_edge "
            "WHERE upstream_id IN (SELECT id FROM lineage_node WHERE node_key = ?)"
        ),
        "params": ["FAN0001_VIB"],
        "expect_index": None,  # upstream 方向：若 SCAN 则说明缺索引（如实报告）
        "why": "血缘 BFS 每层的边展开查询（lineage/impact.py）",
    },
    {
        "label": "血缘 BFS 反向（按下游找上游）",
        "sql": (
            "SELECT upstream_id, downstream_id FROM lineage_edge "
            "WHERE downstream_id IN (SELECT id FROM lineage_node WHERE node_key = ?)"
        ),
        "params": ["FAN0001_VIB"],
        "expect_index": "ix_edge_downstream",
        "why": "血缘 BFS 反向层展开（lineage/impact.py）",
    },
    {
        "label": "设备唯一键定位",
        "sql": "SELECT id, device_code, device_name FROM device WHERE device_code = ?",
        "params": ["FAN-0001"],
        "expect_index": "device_code",
        "why": "导入查重与 `GET /api/devices` 定位",
    },
    {
        "label": "测点分页（按设备过滤）",
        "sql": (
            "SELECT id, point_code, point_name FROM measure_point "
            "WHERE device_id = ? AND is_deleted = 0 LIMIT 20 OFFSET 0"
        ),
        "params": [1],
        "expect_index": "device_id",
        "why": "`GET /api/devices/{id}/points` 分页查询",
    },
]


def run(db_path: str) -> tuple[list[dict], bool]:
    """跑全部 EXPLAIN QUERY PLAN，返回 (结果列表, 是否全部命中)。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    results: list[dict] = []
    all_hit = True
    try:
        for q in QUERIES:
            plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {q['sql']}", q["params"]).fetchall()
            plan = " || ".join(row["detail"] for row in plan_rows)
            # SQLite 计划语义：
            #   SEARCH tbl USING INDEX x      → 索引定位（最佳）
            #   SCAN tbl USING INDEX x        → 索引序全扫（ORDER BY 借索引免排序，可接受）
            #   SCAN tbl（无 USING INDEX）     → 纯表扫描（未命中）
            #   TEMP B-TREE FOR ORDER BY      → 排序走了临时树（ORDER BY 无索引支撑）
            using_index = ("USING INDEX" in plan) or ("USING COVERING INDEX" in plan)
            pure_scan = ("SCAN " in plan) and ("USING INDEX" not in plan)
            temp_sort = "TEMP B-TREE" in plan
            hit = using_index and not temp_sort
            if not hit:
                all_hit = False
            # expect_index 只作提示（自动生成的索引名可能与书写名不同），
            # 命中与否以 plan 语义为准，不以名字字面匹配为准
            expect_note = ""
            if q["expect_index"] and using_index and q["expect_index"].lower() not in plan.lower():
                expect_note = f"（提示：计划使用了其他索引，期望的 {q['expect_index']} 未出现）"
            results.append(
                {
                    "label": q["label"],
                    "why": q["why"],
                    "plan": plan,
                    "using_index": using_index,
                    "pure_scan": pure_scan,
                    "temp_sort": temp_sort,
                    "expect_index": q["expect_index"],
                    "expect_note": expect_note,
                    "hit": hit,
                }
            )
    finally:
        conn.close()
    return results, all_hit


def main() -> int:
    parser = argparse.ArgumentParser(description="核心 SQL 的 EXPLAIN QUERY PLAN 索引验证")
    parser.add_argument("--db", type=str, default=None, help="库文件路径（默认 config 配置的路径）")
    parser.add_argument("--json", type=str, default=None, help="同时输出 JSON 到指定路径")
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        from g05.core.config import settings

        db_path = str(settings._raw["database"]["database"]["path"])  # noqa: SLF001

    if not Path(db_path).exists():
        print(f"[FAIL] 库文件不存在：{db_path}（先跑 python -m g05 initdb && python -m g05 seed）")
        return 1

    results, all_hit = run(db_path)

    print(f"库：{db_path}")
    print("=" * 72)
    for r in results:
        mark = "✅ INDEX" if r["hit"] else "❌ SCAN"
        print(f"[{mark}] {r['label']}")
        print(f"    用途：{r['why']}")
        print(f"    计划：{r['plan']}")
        if r.get("expect_note"):
            print(f"    {r['expect_note']}")
        print()
    print("=" * 72)
    hit_count = sum(1 for r in results if r["hit"])
    print(f"合计：{hit_count}/{len(results)} 条命中索引")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"db": db_path, "all_hit": all_hit, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON 已写入：{out}")

    if not all_hit:
        print("\n[提示] 存在 SCAN 项：核对 models 里对应查询列是否有索引，缺则补 Index 后重跑。")
    return 0 if all_hit else 1


if __name__ == "__main__":
    raise SystemExit(main())
