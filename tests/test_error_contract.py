"""统一错误体契约的守护测试（issue G05-02）。

冻结的约定：**所有非 2xx 响应统一为 ``{code, message, detail}``**。
这个文件是那个约定的守门人 —— 谁把字段改了、谁把错误码丢了，这里都会红。

维护者：A（架构师）。改动错误码语义必须走 PR 评审。
"""

from __future__ import annotations

import collections

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from g05.main import app
from g05.schemas.errors import (
    CODES,
    DESCRIPTIONS,
    HTTP_STATUS,
    ApiError,
    ErrorCode,
    describe,
    http_status_of,
    is_valid_code,
)

# 用例用的临时路由（挂在独立 app 上，避免污染真实 app 的 OpenAPI）
_probe = FastAPI()


@_probe.get("/api_err")
def _api_err():
    raise ApiError(ErrorCode.DUPLICATE_DEVICE, detail={"device_code": "D001"})


@_probe.get("/bare_http")
def _bare_http():
    raise HTTPException(404, "设备 5 不存在")


@_probe.get("/dict_http")
def _dict_http():
    raise HTTPException(
        409,
        {
            "code": ErrorCode.DEVICE_HAS_ACTIVE_POINTS,
            "message": "设备下仍有 3 个活跃测点",
            "active_points": 3,
        },
    )


@_probe.get("/boom")
def _boom():
    raise ValueError("内部炸了")


@_probe.get("/query")
def _query(page: int = 1):
    return {"page": page}


@pytest.fixture(scope="module")
def client():
    """把探针路由挂到真实 app 上，复用项目自己的异常处理器。"""
    for route in _probe.routes:
        if getattr(route, "path", "") != "/openapi.json":
            app.router.routes.append(route)
    return TestClient(app, raise_server_exceptions=False)


# ---------------- 错误码表自身的完整性 ----------------
def test_error_code_table_is_complete():
    """每个登记的码都必须有 HTTP 映射和一句话说明。"""
    assert CODES, "错误码表不能为空"
    missing_status = [c for c in CODES if c not in HTTP_STATUS]
    missing_desc = [c for c in CODES if c not in DESCRIPTIONS]
    assert not missing_status, f"缺 HTTP 状态映射：{missing_status}"
    assert not missing_desc, f"缺错误说明：{missing_desc}"


def test_error_code_prefix_format():
    """错误码必须形如 G05-1xxx / G05-2xxx / G05-5xxx。"""
    for code in CODES:
        assert code.startswith("G05-"), code
        assert len(code) == 8, code
        assert code[4] in "125", f"{code} 的段位只能是 1(参数)/2(业务)/5(系统)"


def test_error_code_segments_have_consistent_semantics():
    """同一段数字的 HTTP 状态语义要一致。

    1xxx 只能 4xx（客户端改参数就能解决），
    5xxx 只能 5xx（服务端问题）。
    2xxx 是业务规则，404/409/422 都允许 —— 语义由具体错误决定。
    """
    for code in CODES:
        status = HTTP_STATUS[code]
        segment = code[4]
        if segment == "1":
            assert 400 <= status < 500, f"{code} 是参数错误，HTTP 应为 4xx，实际 {status}"
        elif segment == "5":
            assert status >= 500, f"{code} 是系统错误，HTTP 应为 5xx，实际 {status}"


def test_error_code_segments_are_not_empty():
    """三段都要有码，不能只实现其中一段。"""
    by_segment = collections.Counter(c[4] for c in CODES)
    for segment in ("1", "2", "5"):
        assert by_segment[segment] > 0, f"G05-{segment}xxx 段没有任何错误码"


def test_unknown_code_falls_back_to_500():
    """未登记的码一律 500 —— 宁可保守，也不能瞎猜 4xx 让客户端白重试。"""
    assert http_status_of("G05-9999") == 500
    assert not is_valid_code("G05-9999")
    assert describe("G05-9999") == "未登记的错误码"


# ---------------- 错误响应体的实际形状 ----------------
@pytest.mark.parametrize(
    ("path", "want_status", "want_code"),
    [
        ("/api_err", 409, ErrorCode.DUPLICATE_DEVICE),
        ("/bare_http", 404, ErrorCode.DEVICE_NOT_FOUND),
        ("/dict_http", 409, ErrorCode.DEVICE_HAS_ACTIVE_POINTS),
        ("/boom", 500, ErrorCode.INTERNAL_ERROR),
    ],
)
def test_error_body_has_exactly_three_fields(client, path, want_status, want_code):
    """所有错误路径都必须返回 {code, message, detail} 三个字段。"""
    resp = client.get(path)
    body = resp.json()
    assert resp.status_code == want_status
    assert set(body.keys()) == {"code", "message", "detail"}
    assert body["code"] == want_code
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["detail"], dict)


def test_api_error_carries_structured_detail(client):
    """ApiError 的 detail 要原样透出，供客户端做精细判断。"""
    body = client.get("/api_err").json()
    assert body["detail"] == {"device_code": "D001"}


def test_dict_http_exception_extra_keys_go_to_detail(client):
    """HTTPException(detail=dict) 里除 code/message 外的键进 detail。"""
    body = client.get("/dict_http").json()
    assert body["detail"] == {"active_points": 3}
    assert body["message"] == "设备下仍有 3 个活跃测点"


def test_bare_http_exception_keeps_its_message(client):
    """裸 HTTPException 的文字说明要保留，只把 code 补上。"""
    body = client.get("/bare_http").json()
    assert body["message"] == "设备 5 不存在"
    assert body["code"] == ErrorCode.DEVICE_NOT_FOUND


def test_validation_error_is_rewritten_to_400(client):
    """FastAPI 默认的 422 结构不符合本项目约定，要改写成 400 + G05-1001。"""
    resp = client.get("/query", params={"page": "abc"})
    body = resp.json()
    assert resp.status_code == 400
    assert body["code"] == ErrorCode.INVALID_PARAM
    assert set(body.keys()) == {"code", "message", "detail"}
    assert body["detail"]["fields"], "应逐字段说明哪里错了"


def test_internal_error_does_not_leak_internals(client):
    """系统错误不能把内部异常原文吐给客户端（堆栈只进服务端日志）。"""
    body = client.get("/boom").json()
    assert body["code"] == ErrorCode.INTERNAL_ERROR
    assert "内部炸了" not in body["message"]
    assert "内部炸了" not in str(body["detail"])
