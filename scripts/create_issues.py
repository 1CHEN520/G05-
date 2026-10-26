"""把 collab/issues.yaml 批量建成 GitHub Issue（幂等）。

用法：
    # 1. 先空跑，看看会建什么（不联网）
    python scripts/create_issues.py --dry-run

    # 2. 真的建（需要 token：repo 权限即可）
    set GITHUB_TOKEN=ghp_xxx
    python scripts/create_issues.py --repo yourname/g05-asset-catalog --apply

    # 3. 顺带按 roster.yaml 把 issue 指派给负责人
    python scripts/create_issues.py --repo yourname/g05-asset-catalog --apply --assign

设计要点：
    - 幂等：按 issue 标题去重，重复执行不会建出一堆副本
    - 先建标签和里程碑，再建 issue，避免 422 报错
    - 只用标准库 urllib，不额外装依赖
    - 结果写 collab/issues_created.json，方便后续批量更新/关闭
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ISSUES_FILE = ROOT / "collab" / "issues.yaml"
ROLES_FILE = ROOT / "collab" / "roles.yaml"
ROSTER_FILE = ROOT / "collab" / "roster.yaml"
RESULT_FILE = ROOT / "collab" / "issues_created.json"

API = "https://api.github.com"

LABEL_COLORS = {
    "type/feature": "1f6feb",
    "type/design": "8250df",
    "type/docs": "0e8a16",
    "type/test": "d93f0b",
    "type/perf": "fbca04",
    "type/integration": "5319e7",
    "area/scaffold": "c2e0c6",
    "area/api": "bfd4f2",
    "area/model": "f9d0c4",
    "area/lineage": "d4c5f9",
    "area/import": "fef2c0",
    "area/audit": "f0c8c8",
    "area/web": "c7def8",
    "area/qa": "e6e6e6",
    "area/infra": "d1d5da",
    "area/env": "d1d5da",
    "area/spec": "d1d5da",
    "area/ops": "d1d5da",
    "area/reports": "d1d5da",
    "area/retro": "d1d5da",
    "area/demo": "d1d5da",
    "prio/P0": "b60205",
    "prio/P1": "d93f0b",
    "prio/P2": "fbca04",
}


def request(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return exc.code, {"error": detail}


def build_body(it: dict, roles: dict) -> str:
    role = roles[it["role"]]
    lines = [
        f"> 角色：`{it['role']}` {role['title']}"
        + (f"（{role['name']}）" if role.get("name") else ""),
        f"> 里程碑：{it['milestone']} ｜ 预估：{it['estimate']} h",
    ]
    if it["depends"]:
        lines.append("> 前置：" + "、".join(f"`{d}`" for d in it["depends"]))
    lines += [
        "",
        "## 要解决的问题",
        "",
        it["goal"],
        "",
        "## 任务",
        "",
    ]
    lines += [f"- [ ] {t}" for t in it["tasks"]]
    lines += ["", "## 验收标准", ""]
    lines += [f"- [ ] {a}" for a in it["acceptance"]]
    lines += ["", "## 涉及文件", ""]
    lines += [f"- `{f}`" for f in it["files"]]
    lines += [
        "",
        "---",
        "",
        f"<sub>由 `collab/issues.yaml` 生成，编号 `{it['key']}`。改内容请改 yaml 后重跑脚本。</sub>",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="批量创建 GitHub Issue")
    ap.add_argument("--repo", help="owner/name，例如 zhangsan/g05-asset-catalog")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    ap.add_argument("--apply", action="store_true", help="真的创建（默认只打印）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不联网")
    ap.add_argument("--assign", action="store_true", help="按 roster.yaml 指派负责人")
    ap.add_argument("--limit", type=int, default=0, help="只建前 N 个，调试用")
    args = ap.parse_args()

    roles_doc = yaml.safe_load(ROLES_FILE.read_text(encoding="utf-8"))
    issues = yaml.safe_load(ISSUES_FILE.read_text(encoding="utf-8"))
    roles = {r["code"]: r for r in roles_doc["roles"]}
    milestones = roles_doc["milestones"]

    assignee: dict[str, str] = {}
    if ROSTER_FILE.exists():
        roster = yaml.safe_load(ROSTER_FILE.read_text(encoding="utf-8")) or {}
        for code, info in (roster.get("members") or {}).items():
            gh = (info.get("github") or "").lstrip("@")
            if gh:
                assignee[code] = gh

    if args.limit:
        issues = issues[: args.limit]

    if not args.apply or args.dry_run:
        print(f"[dry-run] 将创建 {len(issues)} 个 issue，仓库 {args.repo or '<未指定>'}\n")
        for it in issues:
            who = assignee.get(it["role"], "-")
            print(f"  {it['key']:<8}{it['milestone']:<4}{it['role']:<7}@{who:<12}{it['title']}")
        print("\n加上 --apply 才会真的建。")
        return 0

    if not args.repo or "/" not in args.repo:
        raise SystemExit("--apply 需要 --repo owner/name")
    if not args.token:
        raise SystemExit("缺少 token：设置环境变量 GITHUB_TOKEN，或传 --token")

    owner, name = args.repo.split("/", 1)
    base = f"{API}/repos/{owner}/{name}"

    # ---- 1. 标签 ----
    used_labels = {lb for it in issues for lb in it["labels"]}
    for lb in sorted(used_labels):
        status, body = request(
            "POST",
            f"{base}/labels",
            args.token,
            {"name": lb, "color": LABEL_COLORS.get(lb, "ededed")},
        )
        if status == 201:
            print(f"  + label {lb}")
        elif status == 422:
            pass  # 已存在
        else:
            print(f"  ! label {lb} 失败：{body}")

    # ---- 2. 里程碑 ----
    status, existing_ms = request("GET", f"{base}/milestones?state=all&per_page=100", args.token)
    ms_map = {m["title"]: m["number"] for m in (existing_ms or [])} if status == 200 else {}
    for m in milestones.values():
        title = m["title"]
        if title in ms_map:
            continue
        status, body = request(
            "POST",
            f"{base}/milestones",
            args.token,
            {"title": title, "description": m["goal"], "state": "open"},
        )
        if status == 201:
            ms_map[title] = body["number"]
            print(f"  + milestone {title}")
        else:
            print(f"  ! milestone {title} 失败：{body}")

    # ---- 3. 已存在的 issue 标题（幂等） ----
    existing: set[str] = set()
    for page in range(1, 6):
        status, rows = request(
            "GET", f"{base}/issues?state=all&per_page=100&page={page}", args.token
        )
        if status != 200 or not rows:
            break
        existing |= {r["title"] for r in rows}

    # ---- 4. 建 issue ----
    created: dict[str, dict] = {}
    for it in issues:
        if it["title"] in existing:
            print(f"  = 跳过（已存在）{it['key']}")
            continue
        payload = {
            "title": it["title"],
            "body": build_body(it, roles),
            "labels": it["labels"],
        }
        ms_no = ms_map.get(milestones[it["milestone"]]["title"])
        if ms_no:
            payload["milestone"] = ms_no
        if args.assign and it["role"] in assignee:
            payload["assignees"] = [assignee[it["role"]]]

        status, body = request("POST", f"{base}/issues", args.token, payload)
        if status == 201:
            created[it["key"]] = {"number": body["number"], "url": body["html_url"]}
            print(f"  + {it['key']} #{body['number']} {it['title']}")
        else:
            print(f"  ! {it['key']} 失败：{body}")

    if created:
        RESULT_FILE.write_text(
            json.dumps(created, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已创建 {len(created)} 个 issue，映射写入 {RESULT_FILE.relative_to(ROOT)}")
    else:
        print("\n没有新建（都已存在）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
