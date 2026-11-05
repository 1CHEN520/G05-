"""OpenAPI 契约的守护测试（issue G05-02 验收标准：/docs 里每个端点都有入参、出参、错误码说明）。

这个文件守着一条**很容易悄悄坏掉**的约定：
FastAPI 默认只生成 200/422 两个响应，422 的 body 还是框架默认的
``{"detail": [...]}`` —— 与本项目冻结的 ``{code, message, detail}`` 不一致。

所以 ``main._custom_openapi`` 接管了 openapi()，给所有端点补上标准错误响应。
没有这个测试的话，谁把那段代码删了都不会有人发现。

维护者：A（架构师）。改接口契约必须先跑这里的测试。
"""

from __future__ import annotations

import pytest

from g05.main import app
from g05.schemas.errors import CODES

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture(scope="module")
def spec():
    return app.openapi()


def _operations(spec: dict) -> list[tuple[str, str, dict]]:
    """摊平成 (path, method, operation) 列表。"""
    out = []
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if method in HTTP_METHODS:
                out.append((path, method, op))
    return out


def test_openapi_has_endpoints(spec):
    """先确认有端点，否则后面的断言都是空的。"""
    ops = _operations(spec)
    assert len(ops) >= 15, f"端点只有 {len(ops)} 个，路由注册可能出了问题"


def test_every_endpoint_declares_error_responses(spec):
    """**验收标准原文**：每个端点都有错误码说明。"""
    missing = []
    for path, method, op in _operations(spec):
        codes = set(op.get("responses", {}).keys())
        err_codes = {c for c in codes if c.startswith(("4", "5"))}
        if not err_codes:
            missing.append(f"{method.upper()} {path}")
    assert not missing, f"这些端点没声明任何错误响应：{missing}"


def test_every_endpoint_documents_g05_error_code(spec):
    """每个端点的错误响应里要能看到 G05-xxxx 的具体码。"""
    missing = []
    for path, method, op in _operations(spec):
        descs = " ".join(
            str(r.get("description", "")) for c, r in op.get("responses", {}).items()
            if c.startswith(("4", "5"))
        )
        if "G05-" not in descs:
            missing.append(f"{method.upper()} {path}")
    assert not missing, f"这些端点的错误说明里没有 G05 错误码：{missing}"


def test_all_error_responses_use_error_out_schema(spec):
    """所有非 2xx 响应的 body 模型都必须是统一的 ErrorOut。"""
    ref = "#/components/schemas/ErrorOut"
    bad = []
    for path, method, op in _operations(spec):
        for code, resp in op.get("responses", {}).items():
            if code.startswith("2"):
                continue
            schema = resp.get("content", {}).get("application/json", {}).get("schema")
            if schema != {"$ref": ref}:
                bad.append(f"{method.upper()} {path} → {code}")
    assert not bad, f"这些错误响应的模型不是 ErrorOut：{bad}"


def test_error_out_schema_declares_three_fields(spec):
    """ErrorOut 恰好三个必填字段：code / message / detail。"""
    schema = spec["components"]["schemas"]["ErrorOut"]
    assert set(schema["required"]) == {"code", "message", "detail"}
    assert set(schema["properties"]) == {"code", "message", "detail"}


def test_validation_error_description_mentions_g05_1001(spec):
    """422 的说明要被覆盖成 G05-1001，而不是框架默认的 "Validation Error"。"""
    for path, method, op in _operations(spec):
        resp = op.get("responses", {}).get("422")
        if resp:
            assert "G05-1001" in str(resp.get("description", "")), (
                f"{method.upper()} {path} 的 422 说明没提到 G05-1001"
            )


def test_documented_codes_are_all_registered(spec):
    """文档里出现的错误码都必须在 schemas/errors.py 里登记过。

    反过来更重要：登记的码最好都被文档覆盖到（漏了也不报错，
    但这里记一条提示，避免文档与码表悄悄脱节）。
    """
    documented: set[str] = set()
    for _path, _method, op in _operations(spec):
        for code, resp in op.get("responses", {}).items():
            if not code.startswith(("4", "5")):
                continue
            desc = str(resp.get("description", ""))
            for token in desc.replace("/", " ").replace(",", " ").split():
                if token.startswith("G05-"):
                    documented.add(token)

    unknown = documented - set(CODES)
    assert not unknown, f"文档里出现了未登记的错误码：{sorted(unknown)}"
    assert documented, "文档里一个错误码都没有"


def test_rename_endpoint_documents_conflict_code(spec):
    """改名端点必须写明改名冲突的码（409 G05-2013）——这是业务最容易踩的。"""
    op = spec["paths"]["/api/points/{point_id}/rename"]["post"]
    desc = str(op["responses"]["409"].get("description", ""))
    assert "G05-2013" in desc, f"改名端点的 409 说明缺 G05-2013：{desc}"
