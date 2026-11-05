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
- [ ] 改了清单外的文件，已在 issue 里与 A 确认（文件：__）

## 我做了哪些验证

- [ ] `python -m g05 preflight` → `passed: true`
- [ ] `python -m pytest -q` → 全绿，用例数 __
- [ ] 手工验证（写出具体操作步骤与结果）

### 自动化检查（CI 会跑）

- [ ] `ruff check .` → 零告警
- [ ] `python -m pytest -q --cov=src/g05 --cov-report=term-missing` → 覆盖率报告已核对
- [ ] 核心四模块（models / audit / importers / lineage）行覆盖率 ≥ 70%
      实测：__%（`python scripts/check_coverage_gate.py coverage.xml --threshold 70`）
- [ ] `python scripts/fault_inject.py --all` → 5/5 通过
- [ ] `python scripts/bench.py` → 四类指标已记录，命令见 __

> 说明：以上勾选项对应 `.github/workflows/ci.yml` 的检查项。
> 红灯必须挡住合并，CI 里没有 `continue-on-error`。
> 性能类改动请把 `scripts/bench.py` 的输出贴到「截图 / 数据」一节。

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


def _planned_files(roles: list[dict]) -> list[str]:
    """列出「所有分支上出现过的文件」，用于生成分工契约清单。

    分工清单描述的是**角色该负责什么**，而不是"此刻谁的文件在 main 上"。
    所以这里扫全部分支（而不是只看当前分支），
    保证在 main 上生成和在 feat/c5-qa 上生成的结果一致。

    只做参考：真正的归属判定仍以 roles.yaml 的模式为准（见 build_index）。
    """
    import subprocess

    # 收集所有分支：本地 + 远端
    out = subprocess.run(
        ["git", "branch", "-a", "--format=%(refname)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    refs = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    # 去掉 HEAD 之类的伪引用
    refs = [r for r in refs if r and "->" not in r]

    files: set[str] = set()
    for ref in refs:
        tree = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", ref],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if tree.returncode == 0:
            files.update(ln.strip() for ln in tree.stdout.splitlines() if ln.strip())

    # 加上当前工作区里已存在但还没提交的文件
    try:
        from make_role_bundles import list_files

        files.update(list_files())
    except Exception:  # pragma: no cover - 兜底，不让生成脚本因扫描失败而崩
        pass
    return sorted(files)


def main() -> int:
    roles_doc, issues = load()

    from make_role_bundles import build_index, list_files

    # 分工清单是**契约**：写的是"这个角色该负责哪些文件"，
    # 不能随当前分支上有没有这些文件而变。
    #
    # 所以这里不用 list_files()（它只扫当前工作区），而是把
    # 「roles.yaml 里的归属模式」对「所有分支上出现过的文件」求交集 ——
    # 这样不管在哪个分支生成，B2 都还是 6 个文件、C1 还是 5 个。
    #
    # 未归属检查仍然用 list_files()，因为那要反映当前工作区的真实状态。
    files = list_files()
    index, unassigned = build_index(roles_doc["roles"], files)

    planned = _planned_files(roles_doc["roles"])
    planned_index, _ = build_index(roles_doc["roles"], planned)
    for r in roles_doc["roles"]:
        r["_files"] = planned_index[r["code"]]

    # 统一写 LF：Windows 上 write_text 默认写 CRLF，会让 git diff 显示整文件重写，
    # 污染 review。仓库约定 LF（core.autocrlf=false）。
    def _write(path, content: str) -> None:
        path.write_text(content, encoding="utf-8", newline="\n")

    _write(ROOT / "collab" / "分工文件清单.md", render_roster(roles_doc, issues))
    _write(ROOT / "collab" / "issues.md", render_issues(roles_doc, issues))

    gh = ROOT / ".github"
    (gh / "ISSUE_TEMPLATE").mkdir(parents=True, exist_ok=True)
    _write(gh / "CODEOWNERS", render_codeowners(roles_doc))
    _write(gh / "pull_request_template.md", PR_TEMPLATE)
    _write(gh / "ISSUE_TEMPLATE" / "task.md", ISSUE_TEMPLATE)

    print("已生成：")
    print("  collab/分工文件清单.md")
    print("  collab/issues.md")
    print("  .github/CODEOWNERS")
    print("  .github/pull_request_template.md")
    print("  .github/ISSUE_TEMPLATE/task.md")
    if unassigned:
        print(f"\n警告：{len(unassigned)} 个文件未归属 -> {unassigned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
