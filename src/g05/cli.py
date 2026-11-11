"""G05 命令行入口：``python -m g05 <子命令>``。

子命令：
    preflight   环境自检（验收用，输出 JSON，含 passed 字段）
    initdb      建库建表 + 审计触发器
    seed        灌入演示数据
    serve       启动 HTTP 服务

设计意图：把「环境是否装对」做成一条可执行命令，而不是靠人肉对清单。
答辩现场跑一条 ``python -m g05 preflight`` 就能证明环境可用。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_MODULES = [
    ("sqlalchemy", "SQLAlchemy"),
    ("fastapi", "FastAPI"),
    ("uvicorn", "uvicorn"),
    ("pydantic", "pydantic"),
    ("yaml", "PyYAML"),
    ("openpyxl", "openpyxl"),
    ("multipart", "python-multipart"),
]

REQUIRED_FILES = [
    "config/app.yaml",
    "config/database.yaml",
    "config/logging.yaml",
]

# 校验规则归 C2，骨架阶段可能还没合进来，缺失只算"软失败"
RULES_FILE = "config/rules.yaml"

# 静态资源由 C4 负责，骨架阶段可能还没合进来，缺失只算"软失败"
WEB_ASSETS = [
    "web/index.html",
    "web/css/app.css",
    "web/js/api.js",
    "web/js/pages.js",
    "web/js/lineage.js",
    "web/js/main.js",
]


def _check(name: str, ok: bool, detail: Any = "", soft: bool = False) -> dict:
    item = {"name": name, "ok": bool(ok), "detail": detail}
    if soft:
        item["soft"] = True
    return item


def _check_python() -> dict:
    ver = sys.version_info
    ok = (ver.major, ver.minor) >= (3, 10)
    return _check(
        "python>=3.10",
        ok,
        {
            "version": platform.python_version(),
            "executable": sys.executable,
            "in_pp03": "envs\\pp03" in sys.executable or "envs/pp03" in sys.executable,
        },
    )


def _check_modules() -> dict:
    missing = []
    versions = {}
    for mod, dist in REQUIRED_MODULES:
        try:
            m = __import__(mod)
            versions[dist] = getattr(m, "__version__", "ok")
        except Exception:  # noqa: BLE001 - 自检要吞掉一切异常并记录
            missing.append(dist)
    return _check("依赖导入", not missing, {"missing": missing, "versions": versions})


def _check_files() -> dict:
    missing = [f for f in REQUIRED_FILES if not (PROJECT_ROOT / f).exists()]
    return _check("配置文件", not missing, {"missing": missing, "root": str(PROJECT_ROOT)})


def _check_web() -> dict:
    missing = [f for f in WEB_ASSETS if not (PROJECT_ROOT / f).exists()]
    return _check("静态资源", not missing, {"missing": missing}, soft=True)


def _check_config() -> dict:
    from g05.core.config import settings

    keys = ["app", "database", "logging"]
    got = {k: bool(settings.get(k)) for k in keys}
    got["rules"] = bool(settings.get("rules"))
    return _check("配置加载", all(got[k] for k in keys), got)


def _check_rules() -> dict:
    exists = (PROJECT_ROOT / RULES_FILE).exists()
    return _check("校验规则", exists, {"file": RULES_FILE}, soft=True)


def _check_database() -> dict:
    from sqlalchemy import text

    from g05.core.db import get_engine, session_scope

    try:
        from g05.models import Base  # noqa: F401 - 仅用于探测 models 是否已合入
    except ModuleNotFoundError:
        return _check(
            "数据库与触发器",
            False,
            {"reason": "models 尚未合入（B1 负责），骨架阶段属预期"},
            soft=True,
        )

    engine = get_engine()
    # 建表 **并装触发器**：只 create_all 不装触发器的话，触发器永远缺，
    # 干净环境下 preflight 必然假红。ensure_schema 幂等，重复跑无副作用。
    from g05.core.schema import ensure_schema

    ensure_schema()
    with engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
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
    with session_scope() as db:
        db.execute(text("SELECT 1"))

    expected = {"trg_audit_no_update", "trg_audit_no_delete"}
    missing_triggers = sorted(expected - set(triggers))

    # audit_log 表归 C3。M2 阶段 B1 的 models 先合、C3 的 audit_log 后合，
    # 此时 audit_log 表还不存在，apply_triggers() 按设计跳过它，
    # 两个审计触发器自然装不上 —— 这是**合并顺序的正常中间态**，
    # 不是环境装坏了，所以降级为软失败并提示待 C3 合入。
    # 等 C3 合入后 audit_log 表存在，触发器自动补齐，此检查恢复为 hard。
    if "audit_log" not in tables:
        return _check(
            "数据库与触发器",
            False,
            {
                "reason": "audit_log 表尚未合入（C3 负责），审计触发器待其合入后自动补齐",
                "journal_mode": journal,
                "triggers": triggers,
                "missing_triggers": missing_triggers,
                "pending_owner": "C3",
            },
            soft=True,
        )

    return _check(
        "数据库与触发器",
        journal == "wal" and not missing_triggers,
        {"journal_mode": journal, "triggers": triggers, "missing_triggers": missing_triggers},
    )


def _check_writable() -> dict:
    targets = ["data/db", "data/archive", "logs"]
    result = {}
    for t in targets:
        p = PROJECT_ROOT / t
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".preflight_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            result[t] = True
        except OSError as exc:
            result[t] = f"{type(exc).__name__}: {exc}"
    return _check("目录可写", all(v is True for v in result.values()), result)


def cmd_preflight(_: argparse.Namespace) -> int:
    checks = [
        _check_python(),
        _check_modules(),
        _check_files(),
        _check_web(),
        _check_config(),
        _check_rules(),
        _check_database(),
        _check_writable(),
    ]
    hard_fail = [c for c in checks if not c["ok"] and not c.get("soft")]
    soft_fail = [c for c in checks if not c["ok"] and c.get("soft")]
    passed = not hard_fail
    report = {
        "project": "g05-asset-catalog",
        "passed": passed,
        "hard_failures": [c["name"] for c in hard_fail],
        "soft_failures": [c["name"] for c in soft_fail],
        "note": (
            "soft 项缺失不影响 passed：静态资源由 C4 负责，骨架阶段可能尚未合入"
            if soft_fail
            else ""
        ),
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


def cmd_initdb(args: argparse.Namespace) -> int:
    from g05.core.schema import ensure_schema

    ensure_schema(drop=args.drop)
    print("schema ready")
    return 0


def cmd_seed(_: argparse.Namespace) -> int:
    import runpy
    import sys

    # runpy.run_path 会透传当前 sys.argv（含 "seed" 子命令名）给目标脚本，
    # seed_data.py 的 argparse 会把它当未知参数直接报错（issue #38 联调发现）。
    # 这里把 argv 隔离成目标脚本自己的视角，跑完恢复。
    target = str(PROJECT_ROOT / "scripts" / "seed_data.py")
    argv_backup = sys.argv
    sys.argv = [target]
    try:
        runpy.run_path(target, run_name="__main__")
    finally:
        sys.argv = argv_backup
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from g05.core.config import settings

    host = args.host or settings.get("app.server.host", "127.0.0.1")
    port = args.port or int(settings.get("app.server.port", 8000))
    print(f"G05 服务启动 -> http://{host}:{port}/docs")
    uvicorn.run("g05.main:app", host=host, port=port, reload=args.reload, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="g05", description="G05 设备台账与测点资产目录")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("preflight", help="环境自检，输出 JSON")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("initdb", help="建库建表（含审计触发器）")
    p.add_argument("--drop", action="store_true", help="先删表再建")
    p.set_defaults(func=cmd_initdb)

    p = sub.add_parser("seed", help="灌入演示数据")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("serve", help="启动 HTTP 服务")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
