"""配置中心：统一加载 config/*.yaml，支持环境变量覆盖。

设计要点（配置外置铁律）：
- 所有可调参数来自 YAML，代码中不出现魔法数字
- 环境变量以 ``G05_`` 前缀 + ``__`` 分隔层级，例如 ``G05_SERVER__PORT=9000``
- 全局单例，进程内只加载一次；测试可通过 ``reload()`` 重置
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# 项目根目录：src/g05/core/config.py -> 上溯三级
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"

_ENV_PREFIX = "G05_"
_ENV_SEP = "__"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典，override 优先。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _cast(raw: str) -> Any:
    """把环境变量字符串转成合适的 Python 类型。"""
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"none", "null", ""}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _apply_env_overrides(data: dict, prefix: str = _ENV_PREFIX) -> dict:
    """扫描 G05_XXX__YYY 形式的环境变量并覆盖到配置树。"""
    result = dict(data)
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        path = env_key[len(prefix) :].lower().split(_ENV_SEP)
        cursor = result
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):  # 冲突时放弃该覆盖
                break
        else:
            cursor[path[-1]] = _cast(env_val)
    return result


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


class Settings:
    """只读配置访问器：``settings.app["server"]["port"]`` 或 ``settings.get("server.port")``。"""

    def __init__(self, config_dir: Path = CONFIG_DIR) -> None:
        self._config_dir = config_dir
        self._raw: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        merged: dict[str, Any] = {}
        for name in ("app", "database", "rules", "logging"):
            merged[name] = _load_yaml(self._config_dir / f"{name}.yaml")
        self._raw = _apply_env_overrides(merged)

    # ---- 访问接口 ----
    def get(self, dotted: str, default: Any = None) -> Any:
        """点号路径取值，缺失返回 default。"""
        cursor: Any = self._raw
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    def section(self, name: str) -> dict:
        return self._raw.get(name, {}) or {}

    @property
    def app(self) -> dict:
        return self.section("app")

    @property
    def database(self) -> dict:
        return self.section("database")

    @property
    def rules(self) -> dict:
        return self.section("rules")

    # ---- 派生路径 ----
    @property
    def db_path(self) -> Path:
        raw = self.get("database.database.path", "data/db/g05.db")
        path = Path(raw)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Settings dir={self._config_dir} keys={list(self._raw)}>"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例。"""
    return Settings()


settings = get_settings()
