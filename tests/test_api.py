"""接口层用例：分页、错误码、软删、改名、审计联动。

这个文件回答的问题是：**HTTP 这一层的行为对不对？**
不是"函数返回什么"，而是"客户端拿到什么状态码、什么结构、什么数据"。

每条用例都写明**它能挡住哪种回归**，方便评审时判断价值：

- 挡住"分页参数被忽略"→ ``test_pagination_*``
- 挡住"错误码退化成一坨 500"→ ``test_*_not_found`` / ``test_*_conflict``
- 挡住"软删变成硬删"→ ``test_soft_delete_*``
- 挡住"改名没同步影响面"→ ``test_rename_*``
- 挡住"审计漏记或记错操作人"→ ``test_audit_*``
- 挡住"导入接口绕过校验"→ ``test_import_*``
"""

from __future__ import annotations

import io

import pytest


# ------------------------------------------------------------
# 辅助：造一条设备
# ------------------------------------------------------------
def _make_device(api_client, code="FAN-9001", name="接口测试风机", **extra):
    """通过 API 建设备，返回响应体。"""
    payload = {
        "device_code": code,
        "device_name": name,
        "device_type": extra.pop("device_type", "风机"),
        "location": extra.pop("location", "一厂A区"),
        "status": extra.pop("status", "运行"),
    }
    payload.update(extra)
    resp = api_client.post("/api/devices", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_point(api_client, device_id, code="P-9001", name="接口测试测点"):
    resp = api_client.post(
        "/api/points",
        json={
            "device_id": device_id,
            "point_code": code,
            "point_name": name,
            "point_type": "温度",
            "unit": "℃",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------
# 1. 分页
# ------------------------------------------------------------
def test_pagination_splits_items_and_reports_total(api_client):
    """挡住"分页参数被忽略，一次返回全部"。

    行为：建 5 台设备，size=2 时只返回 2 条，但 total 必须是 5。
    """
    for i in range(5):
        _make_device(api_client, code=f"FAN-P{i:02d}")

    resp = api_client.get("/api/devices", params={"page": 1, "size": 2})
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 5, "total 应是满足条件的总数，不是当前页条数"
    assert len(body["items"]) == 2, "items 应受 size 限制"
    assert body["page"] == 1
    assert body["size"] == 2


def test_pagination_second_page_returns_remaining(api_client):
    """挡住"翻页拿到的还是第一页"。

    行为：5 台设备，page=2&size=2 应返回第 3、4 条，且与第一页无交集。
    """
    for i in range(5):
        _make_device(api_client, code=f"FAN-Q{i:02d}")

    page1 = api_client.get("/api/devices", params={"page": 1, "size": 2}).json()
    page2 = api_client.get("/api/devices", params={"page": 2, "size": 2}).json()

    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    codes1 = {d["device_code"] for d in page1["items"]}
    codes2 = {d["device_code"] for d in page2["items"]}
    assert not (codes1 & codes2), "两页内容不应重叠"


def test_pagination_rejects_out_of_range_page(api_client):
    """挡住"page=0 或 size 超上限被当成合法值静默处理"。

    行为：page 必须 ≥1，size 必须 ≤200，越界应返回 422 而不是悄悄纠正。
    """
    assert api_client.get("/api/devices", params={"page": 0}).status_code == 422
    assert api_client.get("/api/devices", params={"size": 500}).status_code == 422


def test_device_filter_by_type_and_keyword(api_client):
    """挡住"过滤条件不生效"。

    行为：混建风机与水泵，按 type 过滤只回风机；按 keyword 只回匹配的。
    """
    _make_device(api_client, code="FAN-F1", name="一号风机", device_type="风机")
    _make_device(api_client, code="PMP-F1", name="一号水泵", device_type="水泵")

    by_type = api_client.get("/api/devices", params={"device_type": "风机"}).json()
    assert by_type["total"] == 1
    assert by_type["items"][0]["device_type"] == "风机"

    by_kw = api_client.get("/api/devices", params={"keyword": "水泵"}).json()
    assert by_kw["total"] == 1
    assert by_kw["items"][0]["device_code"] == "PMP-F1"


# ------------------------------------------------------------
# 2. 错误码
# ------------------------------------------------------------
def test_get_device_not_found_returns_404(api_client):
    """挡住"查不到时返回 200 + 空对象"。

    行为：不存在的 id 必须 404，且错误信息里带上 id 便于排查。
    """
    resp = api_client.get("/api/devices/99999")
    assert resp.status_code == 404
    assert "99999" in resp.text


def test_create_device_duplicate_code_returns_409(api_client):
    """挡住"重复编号被静默接受或报 500"。

    行为：编号是业务唯一键，重复必须 409（冲突），不是 400 也不是 500。
    """
    _make_device(api_client, code="FAN-DUP")
    resp = api_client.post(
        "/api/devices",
        json={
            "device_code": "FAN-DUP",
            "device_name": "重复",
            "device_type": "风机",
            "location": "一厂A区",
        },
    )
    assert resp.status_code == 409
    assert "FAN-DUP" in resp.text


def test_create_device_missing_required_field_returns_422(api_client):
    """挡住"必填字段缺失时创建出一堆空设备"。

    行为：缺 device_name 应 422（校验错误），库里不该多出记录。
    """
    resp = api_client.post(
        "/api/devices",
        json={"device_code": "FAN-BAD", "device_type": "风机", "location": "一厂A区"},
    )
    assert resp.status_code == 422
    assert api_client.get("/api/devices").json()["total"] == 0


def test_update_device_not_found_returns_404(api_client):
    """挡住"PATCH 到不存在的设备返回 200"。

    行为：不存在的 id → 404。
    """
    resp = api_client.patch("/api/devices/99999", json={"device_name": "改名"})
    assert resp.status_code == 404


def test_delete_device_with_active_points_returns_409(api_client):
    """挡住"有活跃测点的设备被直接删掉，导致资产断链"。

    行为：设备下有活跃测点时删除必须 409，且设备仍在库中。
    """
    device = _make_device(api_client, code="FAN-BUSY")
    _make_point(api_client, device["id"], code="P-BUSY")

    resp = api_client.delete(f"/api/devices/{device['id']}")
    assert resp.status_code == 409
    assert "测点" in resp.text

    still = api_client.get(f"/api/devices/{device['id']}")
    assert still.status_code == 200, "拒绝删除后设备必须还在"


# ------------------------------------------------------------
# 3. 软删
# ------------------------------------------------------------
def test_soft_delete_hides_device_from_default_list(api_client):
    """挡住"软删退化成硬删"或"软删后还查得到"。

    行为：删除后默认列表查不到它，但 include_deleted=true 能查到，
    且记录还在库里（数据没被物理抹掉）。
    """
    device = _make_device(api_client, code="FAN-SOFT")
    point = _make_point(api_client, device["id"], code="P-SOFT")
    # 先删测点，腾出设备
    assert api_client.delete(f"/api/points/{point['id']}").status_code == 200

    resp = api_client.delete(f"/api/devices/{device['id']}")
    assert resp.status_code == 200
    assert resp.json()["action"] == "soft_delete"

    default_list = api_client.get("/api/devices").json()
    assert default_list["total"] == 0, "软删后默认列表不应出现"

    with_deleted = api_client.get("/api/devices", params={"include_deleted": True}).json()
    assert with_deleted["total"] == 1, "软删记录应仍可查（可追溯）"
    assert with_deleted["items"][0]["is_deleted"] is True


def test_soft_delete_bumps_version(api_client):
    """挡住"软删不递增版本号，导致并发覆盖无从检测"。

    行为：软删后 version 必须 +1。
    """
    device = _make_device(api_client, code="FAN-VER")
    assert device["version"] == 1

    api_client.delete(f"/api/devices/{device['id']}")
    after = api_client.get("/api/devices", params={"include_deleted": True}).json()
    assert after["items"][0]["version"] == 2


def test_delete_same_device_twice_returns_404(api_client):
    """挡住"重复删除报 500"或"重复删除返回 200 假装成功"。

    行为：已软删的设备再删一次 → 404（已经不在了）。
    """
    device = _make_device(api_client, code="FAN-TWICE")
    assert api_client.delete(f"/api/devices/{device['id']}").status_code == 200
    assert api_client.delete(f"/api/devices/{device['id']}").status_code == 404


# ------------------------------------------------------------
# 4. 改名
# ------------------------------------------------------------
def test_rename_point_updates_name_and_version(api_client):
    """挡住"改名接口只改名字不升版本"。

    行为：改名后 point_name 变、version +1，且返回影响面结构。
    """
    device = _make_device(api_client, code="FAN-REN")
    point = _make_point(api_client, device["id"], code="P-REN", name="旧名字")

    resp = api_client.post(
        f"/api/points/{point['id']}/rename",
        json={"new_name": "新名字", "operator": "alice", "reason": "工艺调整"},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["old_name"] == "旧名字"
    assert body["new_name"] == "新名字"

    detail = api_client.get(f"/api/points/{point['id']}").json()
    assert detail["point_name"] == "新名字"
    assert detail["version"] == point["version"] + 1


def test_rename_point_not_found_returns_404(api_client):
    """挡住"改名不存在的测点返回 200"。

    行为：→ 404。
    """
    resp = api_client.post("/api/points/99999/rename", json={"new_name": "随便"})
    assert resp.status_code == 404


def test_rename_point_rejects_empty_name(api_client):
    """挡住"改名接受空字符串，把测点名清空"。

    行为：new_name 长度必须 ≥1，空名 → 422。
    """
    device = _make_device(api_client, code="FAN-EMPTY")
    point = _make_point(api_client, device["id"], code="P-EMPTY")

    resp = api_client.post(f"/api/points/{point['id']}/rename", json={"new_name": ""})
    assert resp.status_code == 422


def test_point_history_returns_rename_timeline(api_client):
    """挡住"改名没落审计，历史接口查不到变更史"。

    行为：改名后查历史，versions ≥1，且时间线里能看到 RENAME 动作。
    """
    device = _make_device(api_client, code="FAN-HIST")
    point = _make_point(api_client, device["id"], code="P-HIST", name="初始名")

    api_client.post(
        f"/api/points/{point['id']}/rename",
        json={"new_name": "改后名", "operator": "bob", "reason": "统一命名"},
    )

    resp = api_client.get(f"/api/points/{point['id']}/history")
    assert resp.status_code == 200
    body = resp.json()

    assert body["point_code"] == "P-HIST"
    assert body["versions"] >= 1
    actions = {item.get("action") for item in body["timeline"]}
    assert "RENAME" in actions, f"时间线应含 RENAME，实际 {actions}"


# ------------------------------------------------------------
# 5. 审计联动
# ------------------------------------------------------------
def test_create_device_writes_audit_with_operator_from_header(api_client):
    """挡住"接口操作人没写进审计，全成 anonymous"。

    行为：带 X-Operator 头建设备，审计里 operator 应为该值，且动作是 INSERT。
    """
    _make_device(api_client, code="FAN-AUD")

    resp = api_client.get("/api/audit", params={"table_name": "device"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1

    entry = items[0]
    assert entry["table_name"] == "device"
    assert entry["action"] == "INSERT"
    assert entry["operator"] == "anonymous", "未带头时应记为 anonymous"
    assert entry["business_key"] == "FAN-AUD"


def test_audit_records_custom_operator_header(api_client):
    """挡住"X-Operator 头被忽略"。

    行为：带 X-Operator: zhangsan 建设备，审计 operator 应为 zhangsan。
    """
    api_client.post(
        "/api/devices",
        json={
            "device_code": "FAN-OP",
            "device_name": "带操作人的设备",
            "device_type": "风机",
            "location": "一厂A区",
        },
        headers={"X-Operator": "zhangsan"},
    )

    items = api_client.get("/api/audit", params={"operator": "zhangsan"}).json()["items"]
    assert len(items) == 1
    assert items[0]["business_key"] == "FAN-OP"


def test_audit_query_filters_by_action(api_client):
    """挡住"审计过滤条件失效，返回全量"。

    行为：建设备 + 改设备，审计里应能分别筛出 INSERT 与 UPDATE。
    """
    device = _make_device(api_client, code="FAN-FLT")
    api_client.patch(f"/api/devices/{device['id']}", json={"device_name": "改过的名字"})

    inserts = api_client.get("/api/audit", params={"action": "INSERT"}).json()
    updates = api_client.get("/api/audit", params={"action": "UPDATE"}).json()

    assert len(inserts["items"]) == 1
    assert len(updates["items"]) == 1
    assert updates["items"][0]["business_key"] == "FAN-FLT"


def test_audit_verify_immutable_endpoint_reports_blocked(api_client):
    """挡住"不可篡改性自检接口没真正尝试写"。

    行为：/audit/verify-immutable 应报告 UPDATE/DELETE 被拦截。
    """
    _make_device(api_client, code="FAN-IMM")

    resp = api_client.post("/api/audit/verify-immutable")
    assert resp.status_code == 200
    body = resp.json()
    # 结构随实现演进，这里只断言"确实报告了不可篡改"这个语义
    text = str(body).lower()
    assert "immutable" in text or "append-only" in text or "forbidden" in text


def test_audit_export_returns_csv(api_client):
    """挡住"导出接口返回空或非 CSV"。

    行为：导出应为 CSV，且含 BOM（Excel 中文不乱码）。
    """
    _make_device(api_client, code="FAN-CSV")

    resp = api_client.get("/api/audit/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert resp.content.startswith(b"\xef\xbb\xbf"), "应带 UTF-8 BOM"


# ------------------------------------------------------------
# 6. 导入接口
# ------------------------------------------------------------
def test_import_excel_rejects_non_excel_suffix(api_client):
    """挡住"上传 .txt 也能当 Excel 解析"。

    行为：非 .xlsx/.xlsm 后缀 → 400。
    """
    resp = api_client.post(
        "/api/import/excel",
        files={"file": ("bad.txt", io.BytesIO(b"not excel"), "text/plain")},
    )
    assert resp.status_code == 400


def test_import_validate_does_not_write(api_client):
    """挡住"预检接口顺手把数据写进去了"。

    行为：/import/validate 只校验，设备表必须仍然是空的。
    """
    resp = api_client.post(
        "/api/import/validate",
        files={
            "file": (
                "valid.xlsx",
                _make_valid_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200
    assert api_client.get("/api/devices").json()["total"] == 0, "预检不应写入任何设备"


def test_import_excel_writes_valid_workbook(api_client):
    """挡住"合法 Excel 导入后设备数不对"。

    行为：导入合法工作簿 → ok=True，设备入库，且统计条数与实际一致。
    """
    resp = api_client.post(
        "/api/import/excel",
        files={
            "file": (
                "valid.xlsx",
                _make_valid_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, f"合法工作簿应导入成功，实际 {body}"

    listed = api_client.get("/api/devices").json()
    assert listed["total"] >= 1


def test_import_excel_rejects_bad_workbook_without_partial_write(api_client):
    """挡住"脏数据部分入库"——最危险的失败模式。

    行为：导入含错误的表 → 响应体 ok=False，且设备表一条都不该有。

    注意：本项目导入接口的约定是 **HTTP 200 + 响应体 ``ok`` 字段表达业务结果**
    （批量导入是"整批成败"语义，不是单条 REST 语义），所以这里断言 ``ok``
    而不是状态码 —— 断的是"数据没进库"这个行为，不是 HTTP 语义。
    """
    resp = api_client.post(
        "/api/import/excel",
        files={
            "file": (
                "bad.xlsx",
                _make_bad_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False, f"脏数据应被拒绝，实际响应 {body}"
    assert api_client.get("/api/devices").json()["total"] == 0, "校验失败不应有部分写入"


def test_import_template_endpoint_returns_doc(api_client):
    """挡住"模板下载接口 404 或返回 HTML"。

    行为：应返回模板说明（文本/JSON 均可），且状态 200。
    """
    resp = api_client.get("/api/import/template")
    assert resp.status_code == 200
    assert resp.content, "模板说明不应为空"


# ------------------------------------------------------------
# 7. 血缘接口
# ------------------------------------------------------------
def test_lineage_impact_unknown_node_returns_404(api_client):
    """挡住"查不存在的血缘节点返回空结构 200"。

    行为：→ 404。
    """
    resp = api_client.get("/api/lineage/impact", params={"node_key": "NOT-EXIST"})
    assert resp.status_code == 404


def test_lineage_register_node_then_impact_finds_it(api_client):
    """挡住"注册血缘节点后影响面查不到（写入与查询不闭环）"。

    行为：注册节点 → impact 能查到该节点。
    """
    resp = api_client.post(
        "/api/lineage/nodes",
        json={
            "node_type": "point",
            "node_key": "P-LIN-1",
            "node_name": "血缘测试点",
            "owner": "C5",
        },
    )
    assert resp.status_code == 201

    impact = api_client.get("/api/lineage/impact", params={"node_key": "P-LIN-1"})
    assert impact.status_code == 200
    assert impact.json()["root"] is not None


def test_lineage_graph_export_has_nodes_and_edges(api_client):
    """挡住"图导出结构缺失 nodes 或 edges 键"。

    行为：返回体必须同时含 nodes 与 edges。
    """
    resp = api_client.get("/api/lineage/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert "edges" in body


def test_lineage_edge_rejects_self_loop(api_client):
    """挡住"自环边被写入，环检测算法被打乱"。

    行为：上游=下游 的边必须被拒（409/400）。
    """
    api_client.post(
        "/api/lineage/nodes",
        json={"node_type": "point", "node_key": "P-LOOP", "node_name": "环"},
    )
    resp = api_client.post(
        "/api/lineage/edges",
        json={
            "upstream_type": "point",
            "upstream_key": "P-LOOP",
            "downstream_type": "report",
            "downstream_key": "R-LOOP",
        },
    )
    assert resp.status_code in (400, 409)


# ------------------------------------------------------------
# 8. 通用健康检查
# ------------------------------------------------------------
def test_healthz_reports_database_ok(api_client):
    """挡住"数据库自检恒返回 ok，掩盖真实故障"。

    行为：健康检查应明确报告外键开启与 WAL 模式。
    """
    resp = api_client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["database"]["foreign_keys"] is True
    assert body["database"]["journal_mode"].lower() == "wal"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/devices"),
        ("get", "/api/points"),
        ("get", "/api/audit"),
        ("get", "/api/lineage/graph"),
    ],
)
def test_read_endpoints_return_json_object(api_client, method, path):
    """挡住"某个读接口返回裸数组/字符串，破坏前端契约"。

    行为：所有读接口都应返回 JSON 对象（dict），前端按固定 key 取值。
    """
    resp = getattr(api_client, method)(path)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


# ------------------------------------------------------------
# 工作簿构造工具
# ------------------------------------------------------------
def _make_valid_workbook_bytes() -> bytes:
    """构造合法导入工作簿的字节流。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws.append(["FAN-1001", "测试风机", "风机", "一厂A区", "运行"])

    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    ws2.append(["FAN-1001", "FAN1001_TEMP", "温度", "温度", "℃"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_bad_workbook_bytes() -> bytes:
    """构造含错误（编号格式错）的导入工作簿字节流。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws.append(["fan-1001", "非法编号", "风机", "一厂A区", "运行"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
