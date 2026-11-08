"""C4 接口层守护用例（issue G05-16 / G05-17 / G05-33）。

这个文件守的是 **C4 自己新增或改动的行为**，每条用例都注明它能挡住哪种回归：

- 挡住"路由退回裸 HTTPException，错误码语义错配" → ``test_*_error_code_*``
- 挡住"500 响应泄露堆栈 / 缺 trace_id"          → ``test_unhandled_*``
- 挡住"trace_id 对不上（响应里的号捞不到日志）"   → ``test_trace_id_*``
- 挡住"PATCH 传 null 触发 NOT NULL 变成 500"      → ``test_patch_*``
- 挡住"改名把 operator 记成 body 里的伪造值"      → ``test_rename_*``

与 ``tests/test_api.py``（C5 维护）的分工：那边守通用 HTTP 行为，
这边守 C4 新增的错误码契约与 trace_id 链路。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from g05.main import app
from g05.schemas.errors import ErrorCode

# ------------------------------------------------------------
# 探针路由：制造未预期异常，验证兜底处理器的行为
# ------------------------------------------------------------
_probe = FastAPI()


@_probe.get("/api/_c4_boom")
def _c4_boom():
    raise RuntimeError("内部机密字符串 c4-secret-12345")


@pytest.fixture(scope="module")
def client():
    """复用项目自己的异常处理器，不另起一个 app。"""
    for route in _probe.routes:
        if getattr(route, "path", "") != "/openapi.json":
            app.router.routes.append(route)
    return TestClient(app, raise_server_exceptions=False)


def _make_device(api_client, code="C4-0001", name="C4 测试设备"):
    resp = api_client.post(
        "/api/devices",
        json={
            "device_code": code,
            "device_name": name,
            "device_type": "风机",
            "location": "一厂A区",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------
# 1. 错误码语义（挡住裸 HTTPException 造成的错配）
# ------------------------------------------------------------
def test_point_not_found_uses_point_not_found_code(api_client):
    """测点不存在必须是 G05-2002，不能是"设备不存在"的 G05-2001。

    挡住：路由抛裸 HTTPException(404)，被兜底表按状态码猜成设备错误码。
    """
    resp = api_client.get("/api/points/99999")
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.POINT_NOT_FOUND


def test_device_not_found_uses_device_not_found_code(api_client):
    """设备不存在必须是 G05-2001。"""
    resp = api_client.get("/api/devices/99999")
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.DEVICE_NOT_FOUND


def test_duplicate_device_uses_duplicate_device_code(api_client):
    """设备编号重复必须是 G05-2003。"""
    _make_device(api_client, code="C4-DUP")
    resp = api_client.post(
        "/api/devices",
        json={
            "device_code": "C4-DUP",
            "device_name": "重复",
            "device_type": "风机",
            "location": "一厂A区",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.DUPLICATE_DEVICE


def test_device_with_active_points_uses_active_points_code(api_client):
    """设备下有活跃测点时删除，必须是 G05-2005 而不是 G05-2003。

    挡住：两种业务冲突都是 409，裸 HTTPException 分不清，
    客户端拿到"编号已存在"去提示用户"改个编号"会非常荒唐。
    """
    device = _make_device(api_client, code="C4-BUSY")
    api_client.post(
        "/api/points",
        json={
            "device_id": device["id"],
            "point_code": "C4-BUSY-P",
            "point_name": "活跃测点",
            "point_type": "温度",
            "unit": "℃",
        },
    )
    resp = api_client.delete(f"/api/devices/{device['id']}")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == ErrorCode.DEVICE_HAS_ACTIVE_POINTS
    assert body["detail"]["active_points"] >= 1


def test_duplicate_point_uses_duplicate_point_code(api_client):
    """测点编号重复必须是 G05-2004。"""
    device = _make_device(api_client, code="C4-PTP")
    api_client.post(
        "/api/points",
        json={
            "device_id": device["id"],
            "point_code": "C4-PTP-P",
            "point_name": "测点",
            "point_type": "温度",
            "unit": "℃",
        },
    )
    resp = api_client.post(
        "/api/points",
        json={
            "device_id": device["id"],
            "point_code": "C4-PTP-P",
            "point_name": "重复测点",
            "point_type": "温度",
            "unit": "℃",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.DUPLICATE_POINT


def test_point_with_missing_device_uses_point_device_not_found_code(api_client):
    """测点引用的设备不存在必须是 G05-2006 + 404。

    挡住：抛 400（参数错误）—— 设备不存在不是"参数格式错"，
    客户端改参数没用，是资源引用错了。
    """
    resp = api_client.post(
        "/api/points",
        json={
            "device_id": 88888,
            "point_code": "C4-ORPHAN",
            "point_name": "孤儿测点",
            "point_type": "温度",
            "unit": "℃",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.POINT_DEVICE_NOT_FOUND


def test_rename_conflict_uses_rename_conflict_code(api_client):
    """改名冲突必须是 G05-2013。"""
    device = _make_device(api_client, code="C4-CONF")
    point = api_client.post(
        "/api/points",
        json={
            "device_id": device["id"],
            "point_code": "C4-CONF-P",
            "point_name": "同名",
            "point_type": "温度",
            "unit": "℃",
        },
    ).json()
    resp = api_client.post(f"/api/points/{point['id']}/rename", json={"new_name": "同名"})
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.RENAME_CONFLICT


def test_error_body_keeps_frozen_three_field_shape(api_client):
    """所有错误响应仍是 {code, message, detail} 三字段。

    trace_id 放在 detail 里而不是顶层 —— 顶层三字段是 A 冻结的契约，
    加第四个字段会破坏 A 的守护测试与 Swagger 文档。
    """
    resp = api_client.get("/api/devices/99999")
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "detail"}
    assert isinstance(body["detail"], dict)


# ------------------------------------------------------------
# 2. 未预期异常：500 不泄露堆栈，但带 trace_id
# ------------------------------------------------------------
def test_unhandled_error_has_no_stack_trace_in_body(client):
    """500 响应体里不能出现 Python 堆栈或异常原文。

    挡住：把 str(exc) 或 traceback 直接塞进响应 —— 泄露内部结构，
    安全项扣分。
    """
    resp = client.get("/api/_c4_boom")
    body = resp.json()

    assert resp.status_code == 500
    assert body["code"] == ErrorCode.INTERNAL_ERROR
    assert "Traceback" not in resp.text
    assert "c4-secret-12345" not in resp.text
    assert "c4-secret-12345" not in str(body["detail"])


def test_unhandled_error_returns_trace_id(client):
    """500 响应必须带 trace_id —— 用户报障时报这个号。"""
    resp = client.get("/api/_c4_boom")
    body = resp.json()

    assert body["detail"].get("trace_id"), "500 响应必须带 trace_id"
    assert resp.headers.get("X-Trace-Id"), "响应头也必须带 trace_id"


def test_trace_id_in_body_matches_response_header(client):
    """响应体里的 trace_id 与响应头必须一致。

    用户截图里往往只有其中一个（头可能在别处），不一致就没法互相对照。
    """
    resp = client.get("/api/_c4_boom")
    assert resp.json()["detail"]["trace_id"] == resp.headers["X-Trace-Id"]


def test_trace_id_is_stable_within_one_request(api_client):
    """同一请求内多次取 trace_id 必须是同一个值。

    挡住：依赖注入每次调用都新生成 uuid，导致审计记录里的 trace_id
    与错误响应里的对不上，链路就断了。
    """
    from g05.api.deps import get_trace_id

    class _FakeRequest:
        class state:
            trace_id = "c4-fixed-trace-001"

    assert get_trace_id(_FakeRequest(), None) == "c4-fixed-trace-001"


def test_client_supplied_trace_id_is_echoed(api_client):
    """客户端自带 X-Trace-Id 时应被沿用，方便跨服务串联。"""
    resp = api_client.get("/api/devices", headers={"X-Trace-Id": "c4-client-trace-009"})
    assert resp.headers["X-Trace-Id"] == "c4-client-trace-009"


# ------------------------------------------------------------
# 3. PATCH 局部更新（挡住 null 触发 500）
# ------------------------------------------------------------
def test_patch_rejects_null_field_with_param_error(api_client):
    """PATCH 传 null 必须返回 422 而不是 500。

    挡住：exclude_unset 仍会把"显式传的 null"带进变更集，
    setattr 成 None 后在 flush 时撞 NOT NULL 约束 → 500。
    """
    device = _make_device(api_client, code="C4-NULL")
    resp = api_client.patch(f"/api/devices/{device['id']}", json={"device_name": None})

    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.INVALID_PARAM
    assert "device_name" in resp.json()["detail"]["fields"]


def test_patch_updates_only_provided_fields(api_client):
    """PATCH 只改传了的字段，没传的保持原值。

    挡住：用 PUT + 全量 model_dump 局部更新 —— 没传的字段会被清空。
    """
    device = _make_device(api_client, code="C4-PATCH", name="原名称")
    resp = api_client.patch(f"/api/devices/{device['id']}", json={"location": "二厂B区"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["location"] == "二厂B区"
    assert body["device_name"] == "原名称", "没传的字段不该被清空"


def test_patch_bumps_version(api_client):
    """PATCH 后 version 必须 +1（版本链不能断）。"""
    device = _make_device(api_client, code="C4-VER")
    resp = api_client.patch(f"/api/devices/{device['id']}", json={"device_name": "改名了"})
    assert resp.json()["version"] == device["version"] + 1


# ------------------------------------------------------------
# 4. 审计与操作人
# ------------------------------------------------------------
def test_rename_prefers_x_operator_header_over_body_field(api_client):
    """改名时 X-Operator 头优先于 body 里的 operator。

    挡住：body 字段可被随手伪造，头是统一注入的，同一套链路才可信。
    """
    device = _make_device(api_client, code="C4-OP")
    point = api_client.post(
        "/api/points",
        json={
            "device_id": device["id"],
            "point_code": "C4-OP-P",
            "point_name": "旧名",
            "point_type": "温度",
            "unit": "℃",
        },
    ).json()

    resp = api_client.post(
        f"/api/points/{point['id']}/rename",
        json={"new_name": "新名", "operator": "伪造的人"},
        headers={"X-Operator": "tangguoli"},
    )
    assert resp.status_code == 200

    entries = api_client.get("/api/audit", params={"business_key": "C4-OP-P"}).json()["items"]
    renames = [e for e in entries if e["action"] == "RENAME"]
    assert renames, "改名必须落审计"
    assert renames[0]["operator"] == "tangguoli"


def test_rename_falls_back_to_body_operator_without_header(api_client):
    """没带 X-Operator 头时，用 body 里的 operator 兜底。"""
    device = _make_device(api_client, code="C4-OP2")
    point = api_client.post(
        "/api/points",
        json={
            "device_id": device["id"],
            "point_code": "C4-OP2-P",
            "point_name": "旧名",
            "point_type": "温度",
            "unit": "℃",
        },
    ).json()

    api_client.post(
        f"/api/points/{point['id']}/rename",
        json={"new_name": "新名", "operator": "body_operator"},
    )
    entries = api_client.get("/api/audit", params={"business_key": "C4-OP2-P"}).json()["items"]
    renames = [e for e in entries if e["action"] == "RENAME"]
    assert renames[0]["operator"] == "body_operator"


def test_write_operations_record_trace_id(api_client):
    """写操作的审计记录必须带 trace_id，链路才能串起来。"""
    from sqlalchemy import select

    from g05.core.db import session_scope
    from g05.models.audit_log import AuditLog

    _make_device(api_client, code="C4-TID")
    with session_scope() as session:
        entry = session.execute(
            select(AuditLog).where(AuditLog.business_key == "C4-TID")
        ).scalars().first()
        assert entry is not None
        assert entry.trace_id, "审计记录必须带 trace_id"


# ------------------------------------------------------------
# 5. 分页边界
# ------------------------------------------------------------
def test_out_of_range_page_returns_empty_list_not_error(api_client):
    """越界页返回空列表而不是报错 —— 前端翻到第 999 页是正常操作。"""
    for i in range(3):
        _make_device(api_client, code=f"C4-PAGE-{i}")

    resp = api_client.get("/api/devices", params={"page": 999, "size": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] > 0, "total 不该因为翻到越界页而变成 0"
    assert body["page"] == 999


def test_total_uses_same_filters_as_items(api_client):
    """total 必须与 items 用同一组过滤条件。

    挡住：count 忘了加 keyword 过滤 —— total 显示全表数，
    但最后一页翻不到东西。
    """
    _make_device(api_client, code="C4-KW-A", name="一号风机")
    _make_device(api_client, code="C4-KW-B", name="二号水泵")

    body = api_client.get("/api/devices", params={"keyword": "水泵"}).json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["device_name"] == "二号水泵"
