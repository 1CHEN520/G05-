"""自动审计与快照解析的用例。

``tests/test_audit.py`` 覆盖的是**显式审计**（业务层主动调 ``AuditService.record``）。
这个文件补的是另一半：**自动埋点** —— 业务代码什么都不写，
SQLAlchemy 的 flush 事件自动把变更记进审计表。

这条链路一旦断了，症状很隐蔽：功能全对、界面正常，但审计表是空的，
上"变更可追溯"答辩时才发现没有证据。所以这块必须有独立用例盯住。

每条用例能挡住的回归：

- 自动 INSERT 埋点失效 → ``test_auto_audit_captures_insert``
- 自动 UPDATE 埋点失效 → ``test_auto_audit_captures_update``
- 自动 DELETE 埋点失效 → ``test_auto_audit_captures_delete``
- 显式审计被自动埋点重复记一遍 → ``test_skip_audit_flag_suppresses_duplicate``
- 操作人上下文没生效 → ``test_audit_as_sets_operator``
- 旧值快照丢失 → ``test_auto_audit_records_old_value``
- 脏 JSON 让快照解析崩掉 → ``test_parse_snapshot_*``
"""

from __future__ import annotations

import json

from g05.audit.service import (
    AuditService,
    _business_key,
    _jsonable,
    audit_as,
    parse_snapshot,
)
from g05.models.audit_log import AuditLog
from g05.models.device import Device


# ------------------------------------------------------------
# 自动埋点：INSERT
# ------------------------------------------------------------
def test_auto_audit_captures_insert(api_client):
    """挡住"新增设备没进审计表"。

    业务层走 API 新增（内部显式写了审计），这里再补一条直接 ORM 写入的场景：
    直接 ``session.add`` 而不显式记审计，自动埋点必须补上 INSERT 记录。
    """
    from g05.core.db import session_scope

    with session_scope() as db:
        db.add(
            Device(
                device_code="AUTO-INS-1",
                device_name="自动埋点设备",
                device_type="电机",
                location="四厂F区",
            )
        )

    items = api_client.get("/api/audit", params={"action": "INSERT"}).json()["items"]
    keys = {item["business_key"] for item in items}
    assert "AUTO-INS-1" in keys, f"自动埋点应记录 INSERT，实际记了 {keys}"


# ------------------------------------------------------------
# 自动埋点：UPDATE
# ------------------------------------------------------------
def test_auto_audit_captures_update(api_client):
    """挡住"改了设备但审计里只有 INSERT，没有 UPDATE"。

    这是最容易被忽略的一种：更新走的是 dirty 分支，
    如果 before_flush 里 dirty 的判断写错，审计就只剩创建记录。
    """
    from g05.core.db import session_scope

    with session_scope() as db:
        device = Device(
            device_code="AUTO-UPD-1",
            device_name="改之前",
            device_type="电机",
            location="四厂F区",
        )
        db.add(device)
        db.flush()
        device.device_name = "改之后"

    items = api_client.get("/api/audit", params={"action": "UPDATE"}).json()["items"]
    keys = {item["business_key"] for item in items}
    assert "AUTO-UPD-1" in keys, f"自动埋点应记录 UPDATE，实际记了 {keys}"


def test_auto_audit_records_old_value(api_client):
    """挡住"UPDATE 审计里没有旧值，无法还原变更前状态"。

    行为：old_value 里应能看到改之前的名字，新值里是改之后的。

    注意场景：必须走"先提交 → 新 session 加载 → 修改"的真实路径。
    ``loaded_as_persistent`` 事件只在对象**从数据库加载**时拍旧快照；
    同一 session 里 ``add()`` 出来的新对象走 ``session.new`` 分支，
    没有旧快照可拍 —— 那是设计如此，不是缺陷。
    """
    from g05.core.db import session_scope

    with session_scope() as db:
        db.add(
            Device(
                device_code="AUTO-OLD-1",
                device_name="原始名字",
                device_type="电机",
                location="四厂F区",
            )
        )

    # 新会话：从数据库加载（触发旧快照），再修改
    with session_scope() as db:
        device = db.query(Device).filter_by(device_code="AUTO-OLD-1").one()
        device.device_name = "修改后名字"
        db.flush()

    items = api_client.get("/api/audit", params={"business_key": "AUTO-OLD-1"}).json()["items"]
    updates = [i for i in items if i["action"] == "UPDATE"]
    assert updates, "应有 UPDATE 记录"

    entry = updates[0]
    old = json.loads(entry["old_value"]) if entry["old_value"] else {}
    new = json.loads(entry["new_value"]) if entry["new_value"] else {}
    assert old.get("device_name") == "原始名字", f"旧值应保留改前名字，实际 {old}"
    assert new.get("device_name") == "修改后名字", f"新值应是改后名字，实际 {new}"


# ------------------------------------------------------------
# 自动埋点：DELETE
# ------------------------------------------------------------
def test_auto_audit_captures_delete(api_client):
    """挡住"删除动作没进审计表"。

    行为：删掉设备后，审计里应有 DELETE 记录，且带旧值快照。
    """
    from g05.core.db import session_scope

    with session_scope() as db:
        device = Device(
            device_code="AUTO-DEL-1",
            device_name="待删除设备",
            device_type="电机",
            location="四厂F区",
        )
        db.add(device)
        db.flush()
        db.delete(device)

    items = api_client.get("/api/audit", params={"action": "DELETE"}).json()["items"]
    keys = {item["business_key"] for item in items}
    assert "AUTO-DEL-1" in keys, f"自动埋点应记录 DELETE，实际记了 {keys}"


# ------------------------------------------------------------
# 去重：显式审计 + 自动埋点不能记两遍
# ------------------------------------------------------------
def test_skip_audit_flag_suppresses_duplicate(api_client):
    """挡住"一次改名记了两条审计"。

    业务层显式写了带 reason 的审计，就不该再被自动埋点补一条。
    结果应当是**恰好一条** RENAME —— 多一条是噪音，少一条是丢证据。
    """
    device = api_client.post(
        "/api/devices",
        json={
            "device_code": "DUP-1",
            "device_name": "去重设备",
            "device_type": "风机",
            "location": "一厂A区",
        },
    ).json()

    point = api_client.post(
        "/api/points",
        json={
            "device_id": device["id"],
            "point_code": "DUP-P1",
            "point_name": "去重测点",
            "point_type": "温度",
            "unit": "℃",
        },
    ).json()

    api_client.post(
        f"/api/points/{point['id']}/rename",
        json={"new_name": "去重改名", "operator": "carol", "reason": "统一命名"},
    )

    items = api_client.get(
        "/api/audit", params={"business_key": "DUP-P1", "action": "RENAME"}
    ).json()["items"]
    assert len(items) == 1, f"改名应恰好记 1 条审计，实际 {len(items)} 条：{items}"
    assert items[0]["operator"] == "carol"
    assert items[0]["reason"] == "统一命名"


# ------------------------------------------------------------
# 操作人上下文
# ------------------------------------------------------------
def test_audit_as_sets_operator(api_client):
    """挡住"操作人上下文没生效，审计全记成 system"。

    行为：``audit_as`` 包裹期间的 flush，operator 应是被包裹的值。

    注意：必须在 ``audit_as`` **内部** flush。自动埋点挂在 ``before_flush``
    事件上，而 ``session_scope`` 是在退出时才 commit —— 若等到退出才 flush，
    上下文早已结束，operator 会回落成 system。
    """
    from g05.core.db import session_scope

    with session_scope() as db, audit_as("zhangsan"):
        db.add(
            Device(
                device_code="OP-CTX-1",
                device_name="上下文设备",
                device_type="电机",
                location="四厂F区",
            )
        )
        db.flush()

    items = api_client.get("/api/audit", params={"operator": "zhangsan"}).json()["items"]
    keys = {item["business_key"] for item in items}
    assert "OP-CTX-1" in keys, f"audit_as 内写入应记 zhangsan，实际 {keys}"


def test_audit_as_restores_previous_operator(api_client):
    """挡住"audit_as 退出后没恢复，污染后续所有审计"。

    行为：嵌套 audit_as 后，内层结束应恢复外层的值 ——
    内层写入记 inner，外层之后的写入应记回 outer。
    """
    from g05.core.db import session_scope

    with session_scope() as db, audit_as("outer"), audit_as("inner"):
        db.add(
            Device(
                device_code="OP-NEST-1",
                device_name="嵌套设备",
                device_type="电机",
                location="四厂F区",
            )
        )
        db.flush()

    # 内层 audit_as 已退出，此时操作人应恢复为 outer
    with session_scope() as db, audit_as("outer"):
        db.add(
            Device(
                device_code="OP-NEST-2",
                device_name="外层设备",
                device_type="电机",
                location="四厂F区",
            )
        )
        db.flush()

    inner = api_client.get("/api/audit", params={"operator": "inner"}).json()["items"]
    outer = api_client.get("/api/audit", params={"operator": "outer"}).json()["items"]

    inner_keys = {i["business_key"] for i in inner}
    outer_keys = {i["business_key"] for i in outer}
    assert "OP-NEST-1" in inner_keys, f"内层操作人应生效，实际 {inner_keys}"
    assert "OP-NEST-2" in outer_keys, f"内层退出后应恢复 outer，实际 {outer_keys}"
    assert "OP-NEST-2" not in inner_keys, "外层写入不应记成 inner"


def test_audit_as_empty_operator_falls_back_to_anonymous(api_client):
    """挡住"空操作人写成空字符串，审计里出现无名氏"。

    行为：传空串应回落成 anonymous，不能是 ""。
    """
    from g05.core.db import session_scope

    with session_scope() as db, audit_as(""):
        db.add(
            Device(
                device_code="OP-EMPTY-1",
                device_name="空操作人设备",
                device_type="电机",
                location="四厂F区",
            )
        )
        db.flush()

    items = api_client.get("/api/audit", params={"operator": "anonymous"}).json()["items"]
    assert any(i["business_key"] == "OP-EMPTY-1" for i in items), "空操作人应回落 anonymous"


# ------------------------------------------------------------
# 快照解析
# ------------------------------------------------------------
def test_parse_snapshot_returns_dict():
    """挡住"快照解析返回字符串而不是 dict"。

    行为：合法 JSON 字符串应解析成 dict。
    """
    parsed = parse_snapshot('{"a": 1}')
    assert parsed == {"a": 1}


def test_parse_snapshot_none_returns_none():
    """挡住"None 被当成空 dict，调用方判空逻辑失效"。

    行为：空值应返回 None，语义与"没有快照"一致。
    """
    assert parse_snapshot(None) is None
    assert parse_snapshot("") is None


def test_parse_snapshot_invalid_json_keeps_raw():
    """挡住"脏 JSON 让整个审计查询崩掉"。

    行为：非法 JSON 不应抛异常，退化成 {"_raw": 原值}，保证查询接口不崩。
    """
    parsed = parse_snapshot("not-a-json")
    assert parsed == {"_raw": "not-a-json"}


# ------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------
def test_jsonable_converts_datetime_to_iso():
    """挡住"datetime 不能 JSON 序列化，审计写入报错"。

    行为：datetime 应转成 ISO 字符串。
    """
    from datetime import datetime

    result = _jsonable(datetime(2026, 1, 2, 3, 4, 5))
    assert isinstance(result, str)
    assert result.startswith("2026-01-02T03:04:05")


def test_jsonable_passes_through_plain_values():
    """挡住"普通值被误改类型"。

    行为：非 datetime 值原样返回。
    """
    assert _jsonable("abc") == "abc"
    assert _jsonable(42) == 42
    assert _jsonable(None) is None


def test_business_key_prefers_point_code():
    """挡住"业务键取错，审计条目挂不到正确实体上"。

    行为：测点用 point_code，设备用 device_code。
    """
    from g05.models.measure_point import MeasurePoint

    device = Device(device_code="BK-D1", device_name="x", device_type="电机", location="四厂F区")
    assert _business_key(device) == "BK-D1"

    point = MeasurePoint(
        device_id=1, point_code="BK-P1", point_name="y", point_type="温度", unit="℃"
    )
    assert _business_key(point) == "BK-P1"


# ------------------------------------------------------------
# 查询过滤
# ------------------------------------------------------------
def test_audit_query_filters_by_time_range(api_client):
    """挡住"时间范围过滤失效，返回全量"。

    行为：给一个明显早于全部记录的 start，过滤后应为空。
    """
    from datetime import datetime, timedelta

    _make_audit_entry(api_client, "TIME-1")

    future = (datetime.now() + timedelta(days=1)).isoformat()
    items = api_client.get("/api/audit", params={"start": future}).json()["items"]
    assert items == [], "未来时间之后不应有记录"

    past = (datetime.now() - timedelta(days=1)).isoformat()
    items2 = api_client.get("/api/audit", params={"start": past}).json()["items"]
    assert len(items2) >= 1, "昨天之后应有记录"


def test_audit_query_supports_limit_and_offset(api_client):
    """挡住"分页参数被忽略"。

    行为：写 3 条，limit=1&offset=0 只回 1 条；offset=2 回第 3 条。
    """
    for i in range(3):
        _make_audit_entry(api_client, f"PG-{i}")

    first = api_client.get("/api/audit", params={"limit": 1, "offset": 0}).json()["items"]
    third = api_client.get("/api/audit", params={"limit": 1, "offset": 2}).json()["items"]

    assert len(first) == 1
    assert len(third) == 1
    assert first[0]["id"] != third[0]["id"], "offset 应真的跳过记录"


def test_history_of_filters_by_table_name(api_client):
    """挡住"历史查询没按表过滤，串了别的表的记录"。

    行为：同一个业务键在两张表下各记一条，按表名过滤后应只剩一条。
    """
    from g05.core.db import session_scope

    with session_scope() as db:
        AuditService.record(
            db, table_name="device", action="UPDATE", operator="x", business_key="SHARED-1"
        )
        AuditService.record(
            db, table_name="measure_point", action="UPDATE", operator="x", business_key="SHARED-1"
        )

    with session_scope() as db:
        all_hist = AuditService.history_of(db, "SHARED-1")
        only_point = AuditService.history_of(db, "SHARED-1", table_name="measure_point")

    assert len(all_hist) == 2, "不加表过滤应看到两条"
    assert len(only_point) == 1
    assert only_point[0].table_name == "measure_point"


def test_audit_log_build_sets_defaults(api_client):
    """挡住"AuditLog.build 的默认值被改坏"。

    行为：不传可选字段时，应有合理默认值而不是 None。
    """
    from g05.core.db import session_scope

    with session_scope() as db:
        entry = AuditLog.build(
            table_name="device",
            action="INSERT",
            operator="tester",
        )
        db.add(entry)
        db.flush()
        assert entry.id is not None
        assert entry.changed_at is not None


# ------------------------------------------------------------
# 辅助
# ------------------------------------------------------------
def _make_audit_entry(api_client, business_key: str) -> None:
    """通过接口产生一条审计（建设备会写 INSERT 审计）。"""
    api_client.post(
        "/api/devices",
        json={
            "device_code": business_key,
            "device_name": "审计辅助设备",
            "device_type": "风机",
            "location": "一厂A区",
        },
    )
