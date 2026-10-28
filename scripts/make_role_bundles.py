"""按 collab/roles.yaml 把仓库拆成 9 个可独立推送的文件包。

用法：
    python scripts/make_role_bundles.py            # 生成 dist/roles/<CODE>/
    python scripts/make_role_bundles.py --check    # 只检查归属，不落盘
    python scripts/make_role_bundles.py --zip      # 顺带打成 dist/zips/G05-<CODE>.zip

产出：
    dist/roles/<CODE>/
    ├── _bundle/README.md          该角色的说明 + 推送步骤
    ├── _bundle/COMMIT_MSG.txt     现成的提交信息
    ├── _bundle/任务指导书-<CODE>.md   给组员的 AI 读
    ├── _bundle/AI协作指南-<CODE>.md   给组员本人读
    └── files/                     该角色负责的文件（保持相对路径）
    --zip 时额外产出 dist/zips/G05-<CODE>.zip，一个 zip 就是一个人要的全部东西

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
        # Office/WPS 打开文件时生成的锁文件（~$xxx.xlsx），不属于任何人
        if path.name.startswith("~$"):
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

## 包里另外两份文档（先读这两份，再看代码）

| 文件 | 给谁读 | 怎么用 |
|---|---|---|
| `_bundle/任务指导书-{code}.md` | **你的 AI** | 开工第一步，整篇丢给 AI，让它读完再动手 |
| `_bundle/AI协作指南-{code}.md` | **你自己** | 照着第二节配环境，第四节复制初始化提示词 |

## 怎么用这个包

包结构（`files/` 才是要拷进仓库的内容，`_bundle/` 是给你的说明）：

```
{code}/
├── _bundle/
│   ├── README.md              ← 你正在看的这份
│   ├── COMMIT_MSG.txt         ← 提交信息，直接引用
│   ├── 任务指导书-{code}.md     ← 给 AI 读
│   └── AI协作指南-{code}.md     ← 给你自己读
└── files/                     ← 按原相对路径排好，整目录覆盖即可
```

```bash
# 1. 克隆组长建好的仓库
git clone <仓库地址> g05-asset-catalog
cd g05-asset-catalog

# 2. 建你自己的分支（只建一次）
git checkout -b {branch}

# 3. 把 files/ 里的内容覆盖进仓库根目录（保持相对路径）
cp -r <本目录>/files/* .

# 4. 提交（提交信息已写好，直接引用）
git add -A
git commit -F <本目录>/_bundle/COMMIT_MSG.txt
git push -u origin {branch}

# 5. 到 GitHub 上开 Pull Request，目标分支 main，请求 A 评审
```

## 边界

- 只改这个包里的文件。需要动别人的文件（比如 DTO、配置键）→ 先在 issue 里提，由 A 统一改。
- 遇到必须改契约的情况，走「issue 讨论 → A 改契约 → 你 rebase」的流程，不要自己顺手改。
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
    payload = target / "files"
    meta = target / "_bundle"
    meta.mkdir(parents=True, exist_ok=True)

    for rel in files:
        src = ROOT / rel
        dst = payload / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    filelist = "\n".join(f"- `{f}`" for f in files) or "- （空）"
    bullets = "\n".join(f"- {f}" for f in files[:12]) or "- 无"
    if len(files) > 12:
        bullets += f"\n- 其余 {len(files) - 12} 个文件见本包目录"

    (meta / "README.md").write_text(
        BUNDLE_README.format(count=len(files), filelist=filelist, **role),
        encoding="utf-8",
    )
    (meta / "COMMIT_MSG.txt").write_text(
        COMMIT_MSG.format(
            code_lower=role["code"].lower().replace("-", ""),
            bullets=bullets,
            **role,
        ),
        encoding="utf-8",
    )

    # 把该角色的两份指导书放进 _bundle/，让一个包自成闭环，发的时候不用再单独找文档
    for name in (f"任务指导书-{role['code']}.md", f"AI协作指南-{role['code']}.md"):
        src = ROOT / "guides" / name
        if src.is_file():
            shutil.copy2(src, meta / name)


def make_zip(code: str) -> Path:
    """把一个角色目录打成 dist/zips/G05-<CODE>.zip。"""
    import zipfile

    src = DIST / code
    out_dir = ROOT / "dist" / "zips"
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / f"G05-{code}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, (Path(code) / f.relative_to(src)).as_posix())
    return zpath


def main() -> int:
    parser = argparse.ArgumentParser(description="按角色拆分文件包")
    parser.add_argument("--check", action="store_true", help="只检查归属，不写文件")
    parser.add_argument("--zip", action="store_true", help="同时打包成 dist/zips/G05-<CODE>.zip")
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

    if args.zip:
        print()
        for role in roles:
            zpath = make_zip(role["code"])
            size = zpath.stat().st_size / 1024
            print(f"  {zpath.relative_to(ROOT).as_posix():<28} {size:>7.1f} KB")
        print(f"已生成 9 个压缩包 -> {ROOT / 'dist' / 'zips'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
