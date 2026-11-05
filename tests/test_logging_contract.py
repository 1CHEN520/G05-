"""日志契约的守护测试（issue G05-03 验收标准：日志每行可被 json.loads 解析）。

这个文件守着一条**很容易悄悄坏掉**的约定。

背景：原来的 JSON formatter 用 ``"msg":"%(message)s"`` 模板渲染，
消息里的引号/反斜杠/换行**没有转义**。而 uvicorn 的 access log
天然带引号（``127.0.0.1:52058 - "GET /healthz HTTP/1.1" 200``），
于是整行不再是合法 JSON —— ``json.loads`` 抛错，日志采集管道直接断。

实测踩过：7787 行能解析、6959 行解析失败，全是引号没转义。
这个 bug 潜伏了很久没人发现，因为"日志文件存在"不等于"日志能被解析"。

维护者：A（架构师）。改日志格式必须先跑这里的测试。
"""

from __future__ import annotations

import json
import logging
import sys

import pytest

from g05.core.logging import get_logger, setup_logging


@pytest.fixture(scope="module")
def logger():
    setup_logging()
    return get_logger("test_logging")


# ---------------- 单条记录的合法性 ----------------
def test_plain_message_is_valid_json(logger, tmp_path):
    """普通消息要能解析。"""
    rec = _render(logger, "普通消息")
    payload = json.loads(rec)
    assert payload["msg"] == "普通消息"
    assert payload["level"] == "INFO"
    assert "ts" in payload and "logger" in payload


def test_message_with_double_quotes_is_valid_json(logger):
    """**核心回归**：消息含双引号时必须转义。

    uvicorn access log 就是这种形态 —— 这是踩坑的根源。
    """
    rec = _render(logger, '127.0.0.1:52058 - "GET /healthz HTTP/1.1" 200')
    payload = json.loads(rec)  # 解析不了就会在这里抛
    assert '"GET /healthz HTTP/1.1"' in payload["msg"]
    assert payload["msg"].startswith("127.0.0.1:")


def test_message_with_backslash_is_valid_json(logger):
    """含反斜杠（Windows 路径）也要能解析。"""
    rec = _render(logger, r"路径 C:\Users\test\data.db")
    payload = json.loads(rec)
    assert r"C:\Users\test\data.db" in payload["msg"]


def test_message_with_newline_stays_on_one_line(logger):
    """含换行的消息不能把 JSON 拆成多行 —— 否则按行解析就断了。"""
    rec = _render(logger, "第一行\n第二行")
    payload = json.loads(rec)  # 换行未转义时这行就废了
    assert "\n" in payload["msg"]
    assert rec.count("\n") == 0, "渲染结果必须仍是单行"


def test_chinese_message_is_preserved_not_escaped(logger):
    """中文不该被转成 \\uXXXX —— 要可读（ensure_ascii=False）。"""
    rec = _render(logger, "测点改名影响面")
    payload = json.loads(rec)
    assert payload["msg"] == "测点改名影响面"
    assert "\\u" not in rec


# ---------------- extra_fields 平铺 ----------------
def test_extra_fields_are_flattened_into_data(logger):
    """extra_fields 要进 data 字段，且同样能解析。"""
    rec = _render(logger, "导入开始", extra={"extra_fields": {"rows": 1000}})
    payload = json.loads(rec)
    assert payload["data"] == {"rows": 1000}


def test_extra_fields_with_quotes_are_valid_json(logger):
    """extra_fields 里的引号同样不能破坏 JSON。"""
    rec = _render(
        logger,
        "导入失败",
        extra={"extra_fields": {"error": 'invalid column "device_code"'}},
    )
    payload = json.loads(rec)
    assert payload["data"]["error"] == 'invalid column "device_code"'


def test_log_event_produces_valid_json(logger):
    """log_event 是结构化事件的主入口，也必须产出合法 JSON。"""
    rec = _render(logger, "import.done")
    payload = json.loads(rec)
    assert payload["msg"] == "import.done"


# ---------------- 实际写文件后的逐行解析 ----------------
def test_written_log_file_is_line_by_line_parseable(logger, tmp_path):
    """**验收标准原文**：日志文件每行可被 json.loads 解析。

    写一批"最恶劣"的消息到真实文件 handler，然后逐行解析。
    """
    handler = _json_handler(logger)
    filename = getattr(handler, "baseFilename", None)
    if not filename:  # pragma: no cover - 配置异常时跳过
        pytest.skip("未找到 JSON formatter 的文件 handler")

    # 写到一个临时副本上，不动真实的轮转文件
    lines = []
    for msg in [
        "普通消息",
        '带引号 "GET /healthz HTTP/1.1" 200',
        r"带反斜杠 C:\temp\x",
        "带换行\n第二行",
        "带 tab\t和中文",
        '{"像 JSON 的字符串"}',
    ]:
        lines.append(_render(logger, msg))
    target = tmp_path / "app.jsonl"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    read = [ln.strip() for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(read) == len(lines)
    for i, line in enumerate(read):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"第 {i + 1} 行不是合法 JSON：{exc}\n  {line[:160]}")


# ---------------- 辅助 ----------------
def _make_record(logger: logging.Logger, msg: str, **kwargs) -> logging.LogRecord:
    """造一条 LogRecord。

    ``extra`` 里的 ``extra_fields`` 要挂到 record 上（不是传给 makeRecord 的
    保留字），否则 formatter 拿不到。
    """
    extra = kwargs.pop("extra", None)
    record = logger.makeRecord(
        name=logger.name,
        level=logging.INFO,
        fn=__file__,
        lno=1,
        msg=msg,
        args=(),
        exc_info=None,
        **kwargs,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


def _render(logger: logging.Logger, msg: str, **kwargs) -> str:
    """造一条记录并渲染成最终文本行。"""
    return _json_handler(logger).format(_make_record(logger, msg, **kwargs))


def _json_handler(logger: logging.Logger):
    """取一个用 JSON formatter 的**文件** handler。

    注意要跳过 console handler —— 它用的是人眼可读的 format，
    拿它来测 JSON 合法性会得到误导性的结果。
    """
    fallback = None
    for lg in ("g05", ""):
        for handler in logging.getLogger(lg).handlers:
            if type(handler.formatter).__name__ != "_JsonFormatter":
                continue
            if getattr(handler, "baseFilename", None):
                return handler  # 文件 handler，优先
            fallback = fallback or handler
    if fallback is not None:
        return fallback
    raise AssertionError("未找到 JSON formatter 的 handler")


def test_python_version_is_supported():
    """顺带守住 Python ≥3.10（pyproject 的 requires-python）。"""
    assert sys.version_info >= (3, 10)
