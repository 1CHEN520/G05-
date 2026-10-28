"""从 collab/roles.yaml + collab/issues.yaml 生成协作相关文档与 GitHub 配置。

生成物：
    collab/分工文件清单.md        人看的：谁负责哪些文件
    collab/issues.md             人看的：全部 issue（可按里程碑/角色检索）
    .github/CODEOWNERS           机器用的：按路径自动指派评审人
    .github/pull_request_template.md
    .github/ISSUE_TEMPLATE/task.md

用法：
    python scripts/gen_collab_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 允许 import 同目录脚本
ROLES_FILE = ROOT / "collab" / "roles.yaml"
ISSUES_FILE = ROOT / "collab" / "issues.yaml"
ROSTER_FILE = ROOT / "collab" / "roster.yaml"


def load() -> tuple[dict, list[dict]]:
    roles_doc = yaml.safe_load(ROLES_FILE.read_text(encoding="utf-8"))
    issues = yaml.safe_load(ISSUES_FILE.read_text(encoding="utf-8"))

    # 人员信息来自 Excel 回收后的 roster.yaml，不写进 roles.yaml（保住注释）
    if ROSTER_FILE.exists():
        roster = yaml.safe_load(ROSTER_FILE.read_text(encoding="utf-8")) or {}
        members = roster.get("members") or {}
        for r in roles_doc["roles"]:
            info = members.get(r["code"]) or {}
            r["name"] = info.get("name")
            r["github"] = info.get("github")
            r["hours"] = info.get("hours_per_week")
            r["backup"] = info.get("backup")
    return roles_doc, issues


# ---------------------------------------------------------------- 分工清单
def render_roster(roles_doc: dict, issues: list[dict]) -> str:
    roles = roles_doc["roles"]
    by_role: dict[str, list[dict]] = {r["code"]: [] for r in roles}
    for it in issues:
        by_role[it["role"]].append(it)

    lines = [
        "# G05 分工文件清单",
        "",
        "> 本文件由 `scripts/gen_collab_docs.py` 从 `collab/roles.yaml` 自动生成，**不要手改**。",
        "> 改分工请改 `collab/roles.yaml` 后重新生成。",
        "",
        "## 一、总览",
        "",
        "| 角色 | 层级 | 岗位 | 负责人 | 分支 | 文件数 | Issue 数 | 职责 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in roles:
        owner = r.get("name") or "待填"
        lines.append(
            f"| `{r['code']}` | {r['level']} | {r['title']} | {owner} | "
            f"`{r['branch']}` | {len(r['_files'])} | {len(by_role[r['code']])} | {r['mission']} |"
        )

    lines += ["", "## 二、按角色看文件", ""]
    for r in roles:
        lines.append(f"### `{r['code']}` {r['title']}")
        lines.append("")
        lines.append(f"- 分支：`{r['branch']}`")
        lines.append(f"- 负责人：{r.get('name') or '待填'}")
        lines.append(f"- 需要的能力：{r['skills']}")
        if r.get("mentor"):
            lines.append(f"- 指导人：`{r['mentor']}`")
        lines.append("")
        lines.append("| 文件 |")
        lines.append("|---|")
        for f in r["_files"]:
            lines.append(f"| `{f}` |")
        lines.append("")
        lines.append("对应任务：")
        for it in by_role[r["code"]]:
            lines.append(f"- `{it['key']}` {it['title']}（{it['milestone']}，{it['estimate']}h）")
        lines.append("")

    lines += [
        "## 三、协作规则",
        "",
        "1. **一个文件只有一个主人**。上表没出现的文件，找 A 补归属，不要私自新建目录。",
        "2. **跨模块改动走 PR 讨论**。要改 `src/g05/schemas/dto.py`、`config/*.yaml` 这类公共文件，先在 issue 里说，由 A 改。",
        "3. **PR 只包含自己包里的文件**。`git status` 里出现别人的文件，说明你跑错分支了。",
        "4. **合并顺序按依赖**：M1 骨架 → M2 数据内核（B1 先合）→ M3 功能 → M4 质量与文档。",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- issues.md
def render_issues(roles_doc: dict, issues: list[dict]) -> str:
    roles = {r["code"]: r for r in roles_doc["roles"]}
    milestones = roles_doc["milestones"]

    total_hours = sum(it["estimate"] for it in issues)
    lines = [
        "# G05 任务清单（Issue 全集）",
        "",
        f"> 共 **{len(issues)}** 个 issue，预估 **{total_hours}** 人时。",
        "> 本文件由 `scripts/gen_collab_docs.py` 从 `collab/issues.yaml` 生成，**不要手改**。",
        "",
        "## 工时分布",
        "",
        "| 角色 | 岗位 | Issue 数 | 预估工时 |",
        "|---|---|---|---|",
    ]
    for code, r in roles.items():
        mine = [i for i in issues if i["role"] == code]
        lines.append(f"| `{code}` | {r['short']} | {len(mine)} | {sum(i['estimate'] for i in mine)} h |")

    lines += ["", "| 里程碑 | 周次 | 目标 | Issue 数 |", "|---|---|---|---|"]
    for key, m in milestones.items():
        n = len([i for i in issues if i["milestone"] == key])
        lines.append(f"| {m['title']} | {m['week']} | {m['goal']} | {n} |")

    for key, m in milestones.items():
        lines += ["", f"## {m['title']}（{m['week']}）", "", f"_{m['goal']}_", ""]
        for it in [i for i in issues if i["milestone"] == key]:
            role = roles[it["role"]]
            lines += [
                f"### `{it['key']}` {it['title']}",
                "",
                f"- **负责人**：`{it['role']}` {role['title']}"
                + (f"（{role['name']}）" if role.get("name") else ""),
                f"- **预估**：{it['estimate']} h"
                + (f" ｜ **前置**：{', '.join('`' + d + '`' for d in it['depends'])}" if it["depends"] else ""),
                f"- **标签**：{', '.join(it['labels'])}",
                "",
                f"**要解决的问题**：{it['goal']}",
                "",
                "**任务**",
                "",
            ]
            lines += [f"- [ ] {t}" for t in it["tasks"]]
            lines += ["", "**验收标准**", ""]
            lines += [f"- [ ] {a}" for a in it["acceptance"]]
            lines += ["", "**涉及文件**：" + "、".join(f"`{f}`" for f in it["files"]), ""]

    return "\n".join(lines)


# ---------------------------------------------------------------- CODEOWNERS
def render_codeowners(roles_doc: dict) -> str:
    roles = roles_doc["roles"]
    ready = [r for r in roles if r.get("github")]
    pending = [r for r in roles if not r.get("github")]

    lines = [
        "# CODEOWNERS —— 按路径自动指派评审人",
        "# 语法：<路径模式>  <@GitHub用户名>",
        "#",
        f"# 当前状态：已填写 {len(ready)}/{len(roles)} 人",
    ]
    if pending:
        lines.append("# 未填写：" + "、".join(r["code"] for r in pending))
        lines.append("# 填法：把姓名与 GitHub 用户名填进 docs/团队角色清单.xlsx，")
        lines.append("#       然后执行 python scripts/apply_roster.py --xlsx docs/团队角色清单.xlsx")
    lines.append("")

    lines.append("# 默认：架构师兜底")
    if ready:
        lines.append("*  " + ready[0]["github"])
    else:
        lines.append("# *  @<架构师的 GitHub 用户名>   ← 填完表后取消注释")
    lines.append("")

    for r in roles:
        owner = r.get("github")
        patterns = [p for p in r["owns"] if p != "README.md"]
        lines.append(f"# {r['code']} {r['title']}")
        if not owner:
            lines.append(f"# 待填：{r['code']}")
            continue
        for p in patterns:
            lines.append(f"{p}  {owner}")
        lines.append("")
    return "\n".join(lines)


PR_TEMPLATE = """## 这个 PR 做了什么

<!-- 一句话说清，例：实现测点改名接口，改名后同步返回影响面摘要 -->

关联 issue：`G05-__`

## 我负责的角色

- [ ] 我只改了自己 `dist/roles/<角色>/` 里的文件（`git diff --name-only` 自查过）

## 我做了哪些验证

- [ ] `python -m g05 preflight` → `passed: true`
- [ ] `python -m pytest -q` → 全绿，用例数 __
- [ ] 手工验证（写出具体操作步骤与结果）

## 有没有改契约

- [ ] 没有动 `src/g05/schemas/dto.py` / `config/*.yaml` / 数据库表结构
- [ ] 动了，已在 issue 里与 A 确认（issue 链接：__）

## 截图 / 数据

<!-- 性能类改动必须附前后对比数据；UI 改动必须附截图 -->
"""

ISSUE_TEMPLATE = """---
name: 任务
about: 按分工拆解的执行单元
title: "[角色代号] 一句话说明"
labels: ""
assignees: ""
---

## 要解决的问题

<!-- 为什么做这件事，不做会怎样 -->

## 任务

- [ ]

## 验收标准

<!-- 必须可观测、可复现，例如"万行导入 <30s，附三次实测" -->

- [ ]

## 涉及文件

## 前置依赖
"""


def main() -> int:
    roles_doc, issues = load()

    # 把文件清单注入角色（供 render_roster 使用）
    import subprocess

    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    tracked = set(out.stdout.split()) if out.returncode == 0 else set()

    from make_role_bundles import build_index, list_files

    files = list_files()
    index, unassigned = build_index(roles_doc["roles"], files)
    for r in roles_doc["roles"]:
        r["_files"] = index[r["code"]]

    (ROOT / "collab" / "分工文件清单.md").write_text(
        render_roster(roles_doc, issues), encoding="utf-8"
    )
    (ROOT / "collab" / "issues.md").write_text(render_issues(roles_doc, issues), encoding="utf-8")

    gh = ROOT / ".github"
    (gh / "ISSUE_TEMPLATE").mkdir(parents=True, exist_ok=True)
    (gh / "CODEOWNERS").write_text(render_codeowners(roles_doc), encoding="utf-8")
    (gh / "pull_request_template.md").write_text(PR_TEMPLATE, encoding="utf-8")
    (gh / "ISSUE_TEMPLATE" / "task.md").write_text(ISSUE_TEMPLATE, encoding="utf-8")

    print("已生成：")
    print("  collab/分工文件清单.md")
    print("  collab/issues.md")
    print("  .github/CODEOWNERS")
    print("  .github/pull_request_template.md")
    print("  .github/ISSUE_TEMPLATE/task.md")
    if unassigned:
        print(f"\n警告：{len(unassigned)} 个文件未归属 -> {unassigned}")
    _ = tracked
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
