"""结构化日志（可观测铁律）。

用法::

    from g05.core.logging import get_logger, log_event
    log = get_logger(__name__)
    log.info("import start", extra={"extra_fields": {"file": "a.xlsx", "rows": 1000}})
    log_event(log, "import.done", rows=1000, elapsed_ms=812)
"""

from __future__ import annotations

import logging
import logging.config
import sys
from pathlib import Path
from typing import Any

import yaml

from g05.core.config import PROJECT_ROOT

_CONFIGURED = False


class _ExtraFormatter(logging.Formatter):
    """把 extra={"extra_fields": {...}} 平铺进 JSON 行，便于检索。"""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = getattr(record, "extra_fields", None)
        if not fields:
            return base
        import json

        payload = json.dumps(fields, ensure_ascii=False, default=str)
        # 插到最后一个 } 之前，保持单行 JSON 合法
        return base[:-1] + f',"data":{payload}}}' if base.endswith("}") else f"{base} {payload}"


def setup_logging(config_path: Path | None = None) -> None:
    """按 config/logging.yaml 初始化日志，幂等。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    path = config_path or (PROJECT_ROOT / "config" / "logging.yaml")
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if path.exists():
        with path.open("r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}
        # 把 JSON formatter 换成带 extra 平铺的实现
        logging.config.dictConfig(cfg)
        for handler in logging.getLogger("g05").handlers:
            if isinstance(handler.formatter, logging.Formatter):
                handler.setFormatter(
                    _ExtraFormatter(handler.formatter._fmt, handler.formatter.datefmt)  # noqa: SLF001
                )
    else:  # 兜底：配置文件缺失也要能打日志
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            stream=sys.stderr,
        )

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name if name.startswith("g05") else f"g05.{name}")


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """结构化事件日志：事件名作为 msg，其余字段进 data。"""
    logger.info(event, extra={"extra_fields": fields})


# 便捷：模块导入即完成一次初始化
setup_logging()
