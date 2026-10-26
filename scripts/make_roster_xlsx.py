"""生成《G05 团队角色清单》Excel —— 发给组员填写后回收。

用法：
    python scripts/make_roster_xlsx.py              # 生成空白模板
    python scripts/make_roster_xlsx.py --preserve   # 重新生成，但保留已填的人员信息

产出：docs/团队角色清单.xlsx（3 个 sheet：填写表 / 填写说明 / 角色速览）
回收后：
    python scripts/apply_roster.py --xlsx docs/团队角色清单.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = ROOT / "docs" / "团队角色清单.xlsx"

# 人员填写列（第 7~17 列）：重新生成模板时要原样搬过来的内容
PERSON_COLS = list(range(7, 18))

HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
FILL_ME = PatternFill("solid", fgColor="FFF7E0")      # 需要本人填
FILL_FIXED = PatternFill("solid", fgColor="F2F4F7")   # 已定，别改
THIN = Side(style="thin", color="D0D7DE")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

COLUMNS = [
    ("角色代号", 11, "fixed"),
    ("层级", 7, "fixed"),
    ("岗位名称", 26, "fixed"),
    ("主责模块（一句话）", 34, "fixed"),
    ("文件数", 8, "fixed"),
    ("分支名", 20, "fixed"),
    ("姓名", 11, "fill"),
    ("学号", 13, "fill"),
    ("GitHub 用户名", 17, "fill"),
    ("第二志愿角色", 14, "fill"),
    ("Python 熟练度 1-5", 14, "fill"),
    ("SQL/ORM 熟练度 1-5", 15, "fill"),
    ("前端 HTML/JS 1-5", 14, "fill"),
    ("文档写作 1-5", 12, "fill"),
    ("每周可投入小时", 13, "fill"),
    ("常用系统", 12, "fill"),
    ("备注（时间冲突 / 想学的方向）", 32, "fill"),
]

ROLE_BLURB = {
    "A": "定契约、守骨架、做集成，对最终交付负责",
    "B1": "把业务语义落成不可违背的表结构与约束",
    "B2": "回答「改这个测点，谁会受影响」，且 1 秒内答出来",
    "C1": "万行 Excel 30 秒内入库，出错整批回滚",
    "C2": "脏数据进不来，规则改 YAML 就能改",
    "C3": "任何一次变更都能答出「谁、何时、为什么、改了什么」",
    "C4": "让资产目录能用：接口规范、页面能点",
    "C5": "用可重复的证据证明「能扛住」",
    "C6": "让没参与开发的人也能装起来、跑起来、救回来",
}


def build() -> Workbook:
    spec = yaml.safe_load((ROOT / "collab" / "roles.yaml").read_text(encoding="utf-8"))
    roles = spec["roles"]

    wb = Workbook()

    # ---------------- Sheet 1 填写表 ----------------
    ws = wb.active
    ws.title = "角色清单"

    ws["A1"] = "G05 设备台账与测点资产目录 —— 团队角色清单（请组员填写黄色列）"
    ws["A1"].font = Font(size=14, bold=True, color="1F4E79")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMNS))

    ws["A2"] = (
        "填法：只在浅黄底色的 11 列里填；灰底列是已经定好的角色与文件边界，不要改。"
        "填完把本文件发回给组长，组长执行 scripts/apply_roster.py 即可同步到仓库。"
    )
    ws["A2"].font = Font(size=10, color="5B6470")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(COLUMNS))
    ws.row_dimensions[1].height = 24

    header_row = 4
    for idx, (name, width, _) in enumerate(COLUMNS, start=1):
        c = ws.cell(row=header_row, column=idx, value=name)
        c.fill = HEAD_FILL
        c.font = Font(color="FFFFFF", bold=True, size=10)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.row_dimensions[header_row].height = 34

    for r_i, role in enumerate(roles):
        row = header_row + 1 + r_i
        values = [
            role["code"],
            role["level"],
            role["title"],
            ROLE_BLURB.get(role["code"], role["mission"]),
            None,  # 文件数稍后填
            role["branch"],
        ]
        for c_i, val in enumerate(values, start=1):
            c = ws.cell(row=row, column=c_i, value=val)
            c.fill = FILL_FIXED
            c.border = BORDER
            c.alignment = Alignment(vertical="center", wrap_text=(c_i == 4), horizontal="left")
            c.font = Font(size=10)
        for c_i in range(7, len(COLUMNS) + 1):
            c = ws.cell(row=row, column=c_i, value=None)
            c.fill = FILL_ME
            c.border = BORDER
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.font = Font(size=10)
        ws.row_dimensions[row].height = 30

    last = header_row + len(roles)

    # 文件数用公式从《角色速览》取，避免两处维护
    for i in range(len(roles)):
        row = header_row + 1 + i
        ws.cell(row=row, column=5).value = f"=角色速览!F{3 + i}"

    # ---------------- 数据验证 ----------------
    codes = [r["code"] for r in roles]
    dv_role = DataValidation(
        type="list", formula1='"' + ",".join(codes) + '"', allow_blank=True,
        showDropDown=False, error="请从下拉列表里选一个角色代号", errorTitle="无效输入",
    )
    ws.add_data_validation(dv_role)
    dv_role.add(f"J{header_row + 1}:J{last}")

    dv_score = DataValidation(
        type="list", formula1='"1,2,3,4,5"', allow_blank=True,
        error="1=没写过，5=能独立带人做", errorTitle="请填 1-5",
    )
    ws.add_data_validation(dv_score)
    for col in ("K", "L", "M", "N"):
        dv_score.add(f"{col}{header_row + 1}:{col}{last}")

    dv_os = DataValidation(
        type="list", formula1='"Windows,macOS,Linux"', allow_blank=True,
    )
    ws.add_data_validation(dv_os)
    dv_os.add(f"P{header_row + 1}:P{last}")

    dv_hours = DataValidation(
        type="whole", operator="between", formula1="0", formula2="60", allow_blank=True,
        error="请填 0-60 之间的整数（每周能投入的小时数）", errorTitle="小时数不合法",
    )
    ws.add_data_validation(dv_hours)
    dv_hours.add(f"O{header_row + 1}:O{last}")

    ws.freeze_panes = ws.cell(row=header_row + 1, column=7)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(COLUMNS))}{last}"

    # ---------------- Sheet 2 填写说明 ----------------
    ws2 = wb.create_sheet("填写说明")
    ws2.column_dimensions["A"].width = 4
    ws2.column_dimensions["B"].width = 22
    ws2.column_dimensions["C"].width = 88

    ws2["B2"] = "填写说明"
    ws2["B2"].font = Font(size=14, bold=True, color="1F4E79")

    rows = [
        ("填写范围", "只填《角色清单》里浅黄底色的 11 列。灰底列（角色代号/岗位/主责模块/分支）已经定好，改了会和仓库里的分工对不上。"),
        ("角色代号", "A 组长（1 人）；B1/B2 内核骨干（2 人）；C1~C6 开发执行（6 人）。先认领想做的，再由组长平衡。"),
        ("第二志愿", "填另一个角色代号。作用是：某人进度落后时，第二志愿的人能顶上去，不至于整条线卡死。"),
        ("熟练度 1-5", "1=没写过；2=跟着教程能改；3=能独立完成模块；4=能定位性能/并发问题；5=能带人做、能评审别人的代码。按真实水平填，虚高会害了自己。"),
        ("GitHub 用户名", "填你的 GitHub 账号（不是邮箱）。填了之后会自动生成 CODEOWNERS，你的目录有 PR 时自动指派你评审。"),
        ("每周可投入小时", "按真实情况填。排期时会按这个数字分配任务量，填 20 小时但实际只来 8 小时，坑的是同组的人。"),
        ("备注", "写清楚时间冲突（考试/实习/竞赛）、想学的方向、不能做的事（例如不想碰前端）。这些会写进排期。"),
        ("回收方式", "填完把文件发回组长。组长执行：python scripts/apply_roster.py --xlsx docs/团队角色清单.xlsx"),
        ("生效范围", "执行上面那条命令后，会自动更新：collab/roster.yaml、collab/分工文件清单.md、.github/CODEOWNERS。"),
    ]
    r = 4
    for k, v in rows:
        ws2.cell(row=r, column=2, value=k).font = Font(bold=True, size=10)
        ws2.cell(row=r, column=2).alignment = Alignment(vertical="top")
        c = ws2.cell(row=r, column=3, value=v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(size=10)
        ws2.row_dimensions[r].height = 46
        r += 1

    # ---------------- Sheet 3 角色速览 ----------------
    ws3 = wb.create_sheet("角色速览")
    ws3["A1"] = "角色速览（含文件数与主要交付物）"
    ws3["A1"].font = Font(size=13, bold=True, color="1F4E79")

    heads = ["角色代号", "层级", "岗位", "分支", "Issue 数", "文件数", "主要交付物"]
    for i, h in enumerate(heads, start=1):
        c = ws3.cell(row=2, column=i, value=h)
        c.fill = HEAD_FILL
        c.font = Font(color="FFFFFF", bold=True, size=10)
        c.alignment = Alignment(horizontal="center")
        c.border = BORDER

    issues = yaml.safe_load((ROOT / "collab" / "issues.yaml").read_text(encoding="utf-8"))
    from make_role_bundles import build_index, list_files

    index, _ = build_index(roles, list_files())

    deliverable = {
        "A": "骨架 + 契约 + 集成验收",
        "B1": "四表 + 约束 + 建库脚本",
        "B2": "血缘 BFS + 影响面接口 + 血缘图",
        "C1": "Excel 解析 + 批量入库",
        "C2": "规则引擎 + 评分门禁 + 报告导出",
        "C3": "审计留痕 + 防篡改 + 回溯查询",
        "C4": "CRUD 接口 + 控制台页面",
        "C5": "测试 + 故障注入 + 性能基线",
        "C6": "环境/运行手册 + 演示脚本",
    }
    for i, role in enumerate(roles):
        row = 3 + i
        vals = [
            role["code"],
            role["level"],
            role["title"],
            role["branch"],
            len([x for x in issues if x["role"] == role["code"]]),
            len(index[role["code"]]),
            deliverable.get(role["code"], ""),
        ]
        for c_i, v in enumerate(vals, start=1):
            c = ws3.cell(row=row, column=c_i, value=v)
            c.border = BORDER
            c.font = Font(size=10)
            c.alignment = Alignment(horizontal="center" if c_i <= 6 else "left", vertical="center")
    for col, w in zip("ABCDEFG", [11, 7, 30, 20, 9, 8, 32], strict=False):
        ws3.column_dimensions[col].width = w

    return wb


def fill_from_old(wb: Workbook, old_path: Path) -> int:
    """把旧表里已填的人员信息搬进新表。

    两张表的角色行顺序都由 collab/roles.yaml 决定，因此按行号顺序对齐即可；
    同时校验角色代号是否同名（改名场景下会不同，但顺序不变）。
    """
    old = load_workbook(old_path, data_only=True)
    if "角色清单" not in old.sheetnames:
        return 0
    ws_old = old["角色清单"]
    ws_new = wb["角色清单"]

    moved = 0
    for r in range(5, 14):
        if not ws_old.cell(row=r, column=7).value:
            continue
        for c in PERSON_COLS:
            ws_new.cell(row=r, column=c).value = ws_old.cell(row=r, column=c).value
        moved += 1
    return moved


def main() -> int:
    ap = argparse.ArgumentParser(description="生成团队角色清单 Excel")
    ap.add_argument("--preserve", action="store_true", help="保留已填的人员信息")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb = build()

    if args.preserve and OUT.exists():
        try:
            moved = fill_from_old(wb, OUT)
            print(f"已保留 {moved} 行人员信息")
        except PermissionError:
            print("警告：旧文件被占用（WPS/Excel 打开着），本次未保留已填内容")
            return 1

    wb.save(OUT)
    print(f"已生成 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
