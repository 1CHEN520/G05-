"""按 collab/roles.yaml 把仓库拆成 9 个可独立推送的文件包。

用法：
    python scripts/make_role_bundles.py            # 生成 dist/roles/<CODE>/
    python scripts/make_role_bundles.py --check    # 只检查归属，不落盘

产出：
    dist/roles/A-00/  ... 每份包含该角色负责的文件（保持相对路径）+ README.md + COMMIT_MSG.txt

为什么要拆包：
    组长只往远端推骨架，其余文件由各成员在自己的分支上 `git add` 后提交，
    这样每个 PR 的 diff 天然只包含一个人负责的文件，评审和追溯都清楚。
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLES_FILE = ROOT / "collab" / "roles.yaml"
DIST = ROOT / "dist" / "roles"

EXCLUDE_DIRS = {
    ".git",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp_shots",
    "node_modules",
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}
EXCLUDE_NAMES = {".preflight_probe", "g05.db"}


def list_files() -> list[str]:
    """枚举仓库内应被分配的文件（相对 POSIX 路径）。"""
    out: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in EXCLUDE_DIRS):
            continue
        # 运行时产物：日志文件不进任何人的包（只保留占位 .gitkeep）
        if rel.startswith("logs/") and rel != "logs/.gitkeep":
            continue
        if path.suffix in EXCLUDE_SUFFIX or path.name in EXCLUDE_NAMES:
            continue
        out.append(rel)
    return sorted(out)


def match(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/**"):
            base = pat[:-3]
            if rel == base or rel.startswith(base + "/"):
                return True
        elif fnmatch.fnmatch(rel, pat):
            return True
    return False


def build_index(roles: list[dict], files: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """返回 (角色 → 文件列表, 未归属文件列表)。"""
    owner: dict[str, str] = {}
    conflicts: list[str] = []
    for role in roles:
        for rel in files:
            if match(rel, role["owns"]):
                if rel in owner:
                    conflicts.append(f"{rel}: {owner[rel]} 与 {role['code']} 同时声明")
                    continue
                owner[rel] = role["code"]
    if conflicts:
        raise SystemExit("文件归属冲突，请先修 roles.yaml：\n  " + "\n  ".join(conflicts))
    unassigned = [f for f in files if f not in owner]
    result: dict[str, list[str]] = {r["code"]: [] for r in roles}
    for rel, code in owner.items():
        result[code].append(rel)
    for code in result:
        result[code].sort()
    return result, unassigned


BUNDLE_README = """# {code} · {title}

> 分支：`{branch}`
> 归属层级：{level} 层
> 一句话职责：{mission}

## 你这个包里有什么

共 {count} 个文件：

{filelist}

## 怎么用这个包

```bash
# 1. 克隆组长建好的仓库
git clone <仓库地址> g05-asset-catalog
cd g05-asset-catalog

# 2. 建你自己的分支（只建一次）
git checkout -b {branch}

# 3. 把本包内容覆盖进仓库根目录（保持相对路径）
cp -r <本目录>/* .

# 4. 提交（提交信息已写好，直接引用）
git add -A
git commit -F COMMIT_MSG.txt
git push -u origin {branch}

# 5. 到 GitHub 上开 Pull Request，目标分支 main，请求 A-00 评审
```

## 边界

- 只改这个包里的文件。需要动别人的文件（比如 DTO、配置键）→ 先在 issue 里提，由 A-00 统一改。
- 遇到必须改契约的情况，走「issue 讨论 → A-00 改契约 → 你 rebase」的流程，不要自己顺手改。
"""

COMMIT_MSG = """feat({code_lower}): {title}

归属：{code} · {title}
职责：{mission}

本次提交内容：
{bullets}
"""


def write_bundle(role: dict, files: list[str]) -> None:
    target = DIST / role["code"]
    if target.exists():
        shutil.rmtree(target)
    for rel in files:
        src = ROOT / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    filelist = "\n".join(f"- `{f}`" for f in files) or "- （空）"
    bullets = "\n".join(f"- {f}" for f in files[:12]) or "- 无"
    if len(files) > 12:
        bullets += f"\n- 其余 {len(files) - 12} 个文件见本包目录"

    (target / "README.md").write_text(
        BUNDLE_README.format(
            count=len(files), filelist=filelist, **role
        ),
        encoding="utf-8",
    )
    (target / "COMMIT_MSG.txt").write_text(
        COMMIT_MSG.format(
            code_lower=role["code"].lower().replace("-", ""),
            bullets=bullets,
            **role,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="按角色拆分文件包")
    parser.add_argument("--check", action="store_true", help="只检查归属，不写文件")
    args = parser.parse_args()

    spec = yaml.safe_load(ROLES_FILE.read_text(encoding="utf-8"))
    roles = spec["roles"]
    files = list_files()
    index, unassigned = build_index(roles, files)

    print(f"仓库文件总数：{len(files)}")
    print(f"{'角色':<8}{'文件数':>6}  职责")
    print("-" * 62)
    for role in roles:
        print(f"{role['code']:<8}{len(index[role['code']]):>6}  {role['short']}")
    print("-" * 62)

    if unassigned:
        print(f"\n未归属文件 {len(unassigned)} 个（需要补进 roles.yaml）：")
        for f in unassigned:
            print(f"  - {f}")

    if args.check:
        return 0 if not unassigned else 1

    DIST.mkdir(parents=True, exist_ok=True)
    for role in roles:
        write_bundle(role, index[role["code"]])
    print(f"\n已生成 9 个文件包 -> {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
