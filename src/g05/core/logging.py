"""结构化日志（可观测铁律）。

用法::

    from g05.core.logging import get_logger, log_event
    log = get_logger(__name__)
    log.info("import start", extra={"extra_fields": {"file": "a.xlsx", "rows": 1000}})
    log_event(log, "import.done", rows=1000, elapsed_ms=812)
"""

from __future__ import annotations

import json
import logging
import logging.config
import sys
from pathlib import Path
from typing import Any

import yaml

from g05.core.config import PROJECT_ROOT

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """把每条记录渲染成**合法**的单行 JSON。

    为什么自己拼而不用 ``%(message)s`` 模板：
    模板渲染出来的字符串里，消息中的引号/反斜杠/换行**没有转义**，
    一旦日志里出现 ``"GET /healthz HTTP/1.1"`` 这类内容，整行就不是合法 JSON 了 ——
    ``json.loads`` 直接抛错，日志采集管道全断。

    实测踩过的坑：uvicorn 的 access log 天然带引号，
    实测 7787 行能解析、6959 行解析失败，全是引号没转义导致的。

    所以这里统一走 ``json.dumps``，由它负责所有转义。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "msg": record.getMessage(),
        }
        # extra_fields 平铺进 data，便于按字段检索
        fields = getattr(record, "extra_fields", None)
        if fields:
            payload["data"] = fields
        # 异常信息单独放，保证栈也能被解析
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _ExtraFormatter(logging.Formatter):
    """把 extra={"extra_fields": {...}} 平铺进 JSON 行，便于检索。

    保留这个类是为了兼容非 JSON 的 formatter 配置；
    JSON 场景走 :class:`_JsonFormatter`（能正确转义）。
    """

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
        # 把 JSON formatter 换成能正确转义的实现。
        #
        # 不能用 ``%(message)s`` 模板 —— 模板渲染出的字符串里，消息中的
        # 引号/反斜杠/换行**没有转义**，一旦日志里出现
        # ``"GET /healthz HTTP/1.1"`` 这类内容，整行就不是合法 JSON，
        # ``json.loads`` 抛错，日志采集管道全断。
        #
        # 实测踩过的坑：uvicorn 的 access log 天然带引号，
        # 实测 7787 行能解析、6959 行解析失败，全是引号没转义导致的。
        #
        # 所以统一走 ``json.dumps``，由它负责所有转义。
        json_fmt = _JsonFormatter(datefmt=_json_datefmt())
        logging.config.dictConfig(cfg)
        for logger_name in ("g05", "uvicorn", "uvicorn.access", "uvicorn.error", ""):
            for handler in logging.getLogger(logger_name).handlers:
                fmt_name = getattr(handler.formatter, "_fmt", "") or ""
                if '"msg"' in fmt_name:
                    handler.setFormatter(json_fmt)
    else:  # 兜底：配置文件缺失也要能打日志
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            stream=sys.stderr,
        )

    _CONFIGURED = True


class _ExtraFormatter(logging.Formatter):
    """把 extra={"extra_fields": {...}} 平铺进 JSON 行，便于检索。

    保留这个类是为了兼容非 JSON 的 formatter 配置；
    JSON 场景走 :class:`_JsonFormatter`（能正确转义）。
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = getattr(record, "extra_fields", None)
        if not fields:
            return base
        import json

        payload = json.dumps(fields, ensure_ascii=False, default=str)
        # 插到最后一个 } 之前，保持单行 JSON 合法
        return base[:-1] + f',"data":{payload}}}' if base.endswith("}") else f"{base} {payload}"


def _json_datefmt() -> str:
    """JSON formatter 的时间格式（与 config/logging.yaml 保持一致）。"""
    return "%Y-%m-%dT%H:%M:%S%z"


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name if name.startswith("g05") else f"g05.{name}")


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """结构化事件日志：事件名作为 msg，其余字段进 data。"""
    logger.info(event, extra={"extra_fields": fields})


# 便捷：模块导入即完成一次初始化
setup_logging()
