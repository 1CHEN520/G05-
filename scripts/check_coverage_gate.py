"""覆盖率门禁：核心四模块行覆盖率必须 ≥ 阈值。

单独成文件而不是塞在 CI 的 YAML 里，有三个好处：
1. 本地可以先跑一遍，确认门禁真的会红（不是走过场）
2. YAML 缩进错误不会让门禁静默失效
3. 评审时逻辑一目了然，不用在 YAML 里读 Python

用法::

    python scripts/check_coverage_gate.py coverage.xml
    python scripts/check_coverage_gate.py coverage.xml --threshold 70
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# 核心四模块：胶水代码（main / cli / dto）覆盖率天然低，不纳入门禁。
# 指导书技术要点第 8 条：覆盖率看核心模块，不看全局数字。
CORE_PACKAGES = ("models", "audit", "importers", "lineage")

# 低于此值 CI 红灯
DEFAULT_THRESHOLD = 70.0


def collect_package_coverage(xml_path: Path) -> dict[str, tuple[int, int]]:
    """从 coverage.xml 汇总核心四模块的 (命中数, 语句数)。

    coverage.xml 里 ``package`` 的 name 是相对 ``src/g05`` 的路径
    （如 ``models`` / ``audit`` / ``importers`` / ``lineage``），
    不是 ``g05.models`` 这种全限定名 —— 匹配时要注意。

    语句数从 ``<line>`` 子元素个数得出：``class`` 元素上没有
    ``num_statements`` 属性，直接取会得到 0/0，让门禁变成"永远通过"的假绿。
    """
    tree = ET.parse(xml_path)
    result: dict[str, tuple[int, int]] = {}
    for pkg in tree.iter("package"):
        name = pkg.get("name", "").strip(".")
        if name not in CORE_PACKAGES:
            continue
        hits = 0
        stmts = 0
        for cls in pkg.iter("class"):
            for line in cls.iter("line"):
                stmts += 1
                if int(line.get("hits", "0")) > 0:
                    hits += 1
        result[name] = (hits, stmts)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="G05 覆盖率门禁")
    parser.add_argument("xml", nargs="?", default="coverage.xml", help="coverage.xml 路径")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="最低行覆盖率 %%")
    args = parser.parse_args()

    xml_path = Path(args.xml)
    if not xml_path.exists():
        print(f"找不到 {xml_path}，请先跑 pytest --cov-report=xml")
        return 1

    packages = collect_package_coverage(xml_path)
    if not packages:
        print("未找到核心模块覆盖率数据，跳过阈值检查（不阻断）")
        return 0

    print(f"核心四模块覆盖率门禁（阈值 {args.threshold}%）")
    print("-" * 56)
    failed = False
    total_hit = total_stmt = 0
    for name, (hits, stmts) in sorted(packages.items()):
        pct = (hits / stmts * 100) if stmts else 100.0
        flag = "OK " if pct >= args.threshold else "LOW"
        print(f"{flag} {name:<32} {hits:4}/{stmts:4} {pct:5.1f}%")
        if pct < args.threshold:
            failed = True
        total_hit += hits
        total_stmt += stmts

    overall = (total_hit / total_stmt * 100) if total_stmt else 100.0
    print("-" * 56)
    print(f"合计 {total_hit}/{total_stmt} = {overall:.1f}%")
    if failed:
        print(f"核心模块行覆盖率低于 {args.threshold}% —— 门禁失败")
        return 1
    print(f"核心模块覆盖率均 ≥ {args.threshold}% —— 门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
