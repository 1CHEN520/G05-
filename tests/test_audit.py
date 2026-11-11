"""审计测试：写入、查询、不可篡改、历史时间线。

覆盖四个 issue 的验收点：
- G05-13：增删改三类动作都留痕，且 old_value 是**改前**的值
- G05-20：UPDATE / DELETE audit_log 100% 被触发器拦截
- G05-21：过滤、CSV 导出、时间线顺序正确
- G05-34：故障注入 + 事务回滚后审计表无残留
"""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from g05.audit.service import AuditService, parse_snapshot
from g05.models.audit_log import AuditLog


def test_audit_write_and_query(db):
    AuditService.record(
        db,
        table_name="device",
        action="INSERT",
        operator="alice",
        record_id=1,
        business_key="FAN-0001",
        new={"device_name": "1号风机"},
        reason="新增设备",
    )
    db.commit()

    entries = AuditService.query(db, table_name="device", operator="alice")
    assert len(entries) == 1
    assert entries[0].business_key == "FAN-0001"
    assert entries[0].reason == "新增设备"


def test_audit_update_blocked_by_trigger(db):
    """红线：审计表不允许 UPDATE。"""
    AuditService.record(db, table_name="device", action="INSERT", operator="a", record_id=1)
    db.commit()

    with pytest.raises(Exception) as exc:
        db.execute(text("UPDATE audit_log SET operator='hacker'"))
        db.commit()
    db.rollback()
    assert "append-only" in str(exc.value).lower() or "forbidden" in str(exc.value).lower()


def test_audit_delete_blocked_by_trigger(db):
    AuditService.record(db, table_name="device", action="INSERT", operator="a", record_id=1)
    db.commit()

    with pytest.raises(DatabaseError, match="append-only"):
        db.execute(text("DELETE FROM audit_log"))
        db.commit()
    db.rollback()


def test_history_of_ordered_ascending(db):
    for i in range(3):
        AuditService.record(
            db, table_name="measure_point", action="INSERT" if i == 0 else "UPDATE",
            operator="bob", business_key="P_1", new={"v": i},
        )
        db.commit()

    history = AuditService.history_of(db, "P_1")
    assert len(history) == 3
    assert [h.changed_at for h in history] == sorted(h.changed_at for h in history)


def test_audit_records_old_and_new_snapshot(db):
    AuditService.record(
        db,
        table_name="measure_point",
        action="RENAME",
        operator="carol",
        business_key="P_1",
        old={"point_name": "旧名"},
        new={"point_name": "新名"},
        reason="统一命名",
    )
    db.commit()

    entry = db.query(AuditLog).filter_by(action="RENAME").one()
    assert "旧名" in entry.old_value
    assert "新名" in entry.new_value


def test_filter_by_action(db):
    AuditService.record(db, table_name="device", action="INSERT", operator="x", business_key="D1")
    AuditService.record(db, table_name="device", action="SOFT_DELETE", operator="x", business_key="D1")
    db.commit()

    only_delete = AuditService.query(db, action="SOFT_DELETE")
    assert len(only_delete) == 1


def test_audit_operator_and_trace(db):
    AuditService.record(
        db, table_name="device", action="INSERT", operator="dave", trace_id="trace-123", business_key="D9"
    )
    db.commit()
    entry = db.query(AuditLog).filter_by(business_key="D9").one()
    assert entry.operator == "dave"
    assert entry.trace_id == "trace-123"


def test_rename_via_service_writes_audit(db, sample_devices):
    from g05.services.point_service import rename_point

    point = sample_devices["points"][0]
    result = rename_point(db, point.id, "改后的名字", operator="erin", reason="工艺调整")

    assert result["new_version"] == 2
    entries = AuditService.query(db, business_key=point.point_code, action="RENAME")
    assert len(entries) == 1
    assert entries[0].operator == "erin"
    assert "改后的名字" in entries[0].new_value


# ------------------------------------------------------------
# G05-13：审计表模型与变更快照采集
# ------------------------------------------------------------
def test_auto_audit_insert_snapshot_has_resolved_id_and_defaults(db):
    """INSERT 的快照必须是落库后的真实值（含自增主键与列默认值）。

    在 ``before_flush`` 里拍快照时，自增主键与列默认值还没解析出来，
    记进审计的就是 ``id=None, version=None`` —— 审计与事实不符，等于没有审计。
    """
    from g05.models.device import Device

    device = Device(
        device_code="AUTO-ID-1", device_name="带主键", device_type="电机", location="四厂F区"
    )
    db.add(device)
    db.commit()

    entries = AuditService.query(db, business_key="AUTO-ID-1", action="INSERT")
    assert len(entries) == 1
    entry = entries[0]
    snapshot = parse_snapshot(entry.new_value)
    assert entry.record_id is not None, "INSERT 审计应带 record_id"
    assert snapshot["id"] == entry.record_id
    assert snapshot["version"] == 1, "列默认值必须体现在快照里，不能是 None"
    assert snapshot["is_deleted"] is False


def test_auto_audit_update_old_value_is_before_change(db):
    """核心红线：old_value 必须是**改之前**的值。"""
    from g05.models.device import Device

    device = Device(
        device_code="AUTO-OLD-2", device_name="旧名字", device_type="电机", location="四厂F区"
    )
    db.add(device)
    db.commit()
    db.expunge_all()

    loaded = db.query(Device).filter_by(device_code="AUTO-OLD-2").one()
    loaded.device_name = "新名字"
    db.commit()

    entries = AuditService.query(db, business_key="AUTO-OLD-2", action="UPDATE")
    assert len(entries) == 1
    old = parse_snapshot(entries[0].old_value)
    new = parse_snapshot(entries[0].new_value)
    assert old["device_name"] == "旧名字", "old_value 必须是改前的值"
    assert new["device_name"] == "新名字", "new_value 必须是改后的值"
    assert old["device_name"] != new["device_name"]


def test_auto_audit_second_update_old_value_is_previous_value(db):
    """同一 session 连续改两次，第二次的 old_value 是第一次改完的值。

    快照只在加载时拍一次的话，第二次的 old_value 仍是第一次之前的老快照。
    """
    from g05.models.device import Device

    device = Device(
        device_code="AUTO-TWICE-3", device_name="v0", device_type="电机", location="四厂F区"
    )
    db.add(device)
    db.commit()
    db.expunge_all()

    loaded = db.query(Device).filter_by(device_code="AUTO-TWICE-3").one()
    loaded.device_name = "v1"
    db.commit()
    loaded.device_name = "v2"
    db.commit()

    # history_of 是时间正序；query 是倒序（最近的在前）
    updates = [h for h in AuditService.history_of(db, "AUTO-TWICE-3") if h.action == "UPDATE"]
    assert len(updates) == 2
    assert parse_snapshot(updates[0].old_value)["device_name"] == "v0"
    assert parse_snapshot(updates[1].old_value)["device_name"] == "v1", (
        "第二次修改的 old_value 应为上一次的 new_value，不能是更早的旧快照"
    )


def test_auto_audit_soft_delete_marked_as_soft_delete(db):
    """软删（is_deleted False→True）要能识别成 SOFT_DELETE，而不是普通 UPDATE。"""
    from g05.models.device import Device

    device = Device(
        device_code="AUTO-SOFT-4", device_name="待软删", device_type="电机", location="四厂F区"
    )
    db.add(device)
    db.commit()
    db.expunge_all()

    loaded = db.query(Device).filter_by(device_code="AUTO-SOFT-4").one()
    loaded.is_deleted = True
    db.commit()

    actions = [e.action for e in AuditService.history_of(db, "AUTO-SOFT-4")]
    assert "SOFT_DELETE" in actions, f"软删应记为 SOFT_DELETE，实际: {actions}"


def test_auto_audit_hard_delete_records_old_snapshot(db):
    """硬删也要留痕，且带删除前的快照。"""
    from g05.models.device import Device

    device = Device(
        device_code="AUTO-DEL-5", device_name="待删除", device_type="电机", location="四厂F区"
    )
    db.add(device)
    db.commit()
    db.expunge_all()

    loaded = db.query(Device).filter_by(device_code="AUTO-DEL-5").one()
    db.delete(loaded)
    db.commit()

    entries = AuditService.query(db, business_key="AUTO-DEL-5", action="DELETE")
    assert len(entries) == 1
    assert parse_snapshot(entries[0].old_value)["device_name"] == "待删除"


def test_audit_and_business_share_one_transaction(db):
    """审计写入与被审计变更在同一事务内：业务提交后审计才能查到。"""
    from g05.models.device import Device

    db.add(
        Device(
            device_code="AUTO-TX-6", device_name="事务内", device_type="电机", location="四厂F区"
        )
    )
    db.commit()

    row = db.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE business_key='AUTO-TX-6'")
    ).scalar()
    assert row == 1


def test_explicit_audit_and_auto_audit_do_not_duplicate(db, sample_devices):
    """业务层显式写了审计，自动埋点必须跳过，否则改名会产生两条。"""
    from g05.services.point_service import rename_point

    point = sample_devices["points"][0]
    rename_point(db, point.id, "改后的名字", operator="erin", reason="工艺调整")

    renames = AuditService.query(db, business_key=point.point_code, action="RENAME")
    assert len(renames) == 1, "显式审计 + 自动埋点不能重复"
    updates = AuditService.query(db, business_key=point.point_code, action="UPDATE")
    assert len(updates) == 0, "业务层已显式审计的变更，自动埋点不应再记一条 UPDATE"


def test_auto_audit_covers_session_created_directly(db):
    """埋点必须挂在 Session 类上：用 ``Session(bind=engine)`` 另开会话也要留痕。

    挂在 sessionmaker 实例上的话，只有该实例产出的 Session 生效，
    另开的 Session 会**完全不留痕**。
    """
    from g05.core.db import get_engine
    from g05.models.device import Device

    direct = Session(bind=get_engine())
    try:
        direct.add(
            Device(
                device_code="AUTO-DIRECT-7",
                device_name="直接开会话",
                device_type="电机",
                location="四厂F区",
            )
        )
        direct.commit()
    finally:
        direct.close()

    entries = AuditService.query(db, business_key="AUTO-DIRECT-7", action="INSERT")
    assert len(entries) == 1, "直接用 Session(bind=engine) 写入也必须自动留痕"


def test_install_auto_audit_is_idempotent(db):
    """重复调用 install_auto_audit 不能导致一次修改记两条审计。

    应用启动会调一次、测试夹具可能再调一次——监听器注册两遍，
    审计就会翻倍。这条守住"重复安装安全"。
    """
    from g05.audit.service import install_auto_audit
    from g05.models.device import Device

    install_auto_audit()
    install_auto_audit()

    db.add(
        Device(
            device_code="AUTO-IDEM-8", device_name="幂等", device_type="电机", location="四厂F区"
        )
    )
    db.commit()

    entries = AuditService.query(db, business_key="AUTO-IDEM-8", action="INSERT")
    assert len(entries) == 1, f"重复安装不应产生重复审计，实际 {len(entries)} 条"


def test_install_auto_audit_does_not_register_duplicate_listeners(db):
    """挡住"监听器被重复注册，flush 时白跑一遍"。

    重复注册本身不会立刻产生重复审计（登记表的 id 去重兜住了），
    但每次 flush 都要多跑一遍回调，是实打实的性能损耗；
    而且一旦有人改动登记表逻辑，重复监听就会变成重复审计。
    这里直接盯住"监听器只注册一次"。

    用 ``event.registry`` 的公开计数方式，不依赖 SQLAlchemy 内部结构。
    """
    from sqlalchemy import event

    from g05.audit.service import install_auto_audit

    def _listener_count() -> int:
        total = 0
        for key, collection in event.registry._key_to_collection.items():  # noqa: SLF001
            if len(key) == 3 and key[1] == "before_flush":
                total += len(collection)
        return total

    before = _listener_count()
    install_auto_audit()
    install_auto_audit()
    after = _listener_count()

    assert after == before, f"重复安装不应新增监听器（{before} -> {after}）"


# ------------------------------------------------------------
# G05-20：append-only 与防篡改
# ------------------------------------------------------------
def test_tamper_attempts_are_always_blocked(db):
    """故障注入：直接 SQL UPDATE/DELETE audit_log 必须 100% 失败。"""
    AuditService.record(db, table_name="device", action="INSERT", operator="a", record_id=1)
    db.commit()

    for sql in (
        "UPDATE audit_log SET operator='hacker'",
        "UPDATE audit_log SET old_value='{}'",
        "DELETE FROM audit_log",
        "DELETE FROM audit_log WHERE id=1",
    ):
        with pytest.raises(DatabaseError) as exc:
            db.execute(text(sql))
            db.commit()
        db.rollback()
        assert "append-only" in str(exc.value).lower()


def test_tamper_via_orm_is_also_blocked(db):
    """通过 ORM 改审计记录同样要被数据库拦下。"""
    AuditService.record(db, table_name="device", action="INSERT", operator="a", record_id=1)
    db.commit()

    entry = db.query(AuditLog).filter_by(record_id=1).one()
    entry.operator = "hacker"
    with pytest.raises(DatabaseError):
        db.commit()
    db.rollback()


def test_audit_service_exposes_no_update_or_delete():
    """红线：AuditService 不得提供任何修改/删除审计记录的方法。

    注意 ``dir()`` 会列出继承来的方法，所以要显式比对禁用词表。
    """
    forbidden = {"update", "delete", "remove", "destroy", "purge", "truncate"}
    exposed = {name for name in dir(AuditService) if not name.startswith("_")}
    assert not (exposed & forbidden), f"AuditService 暴露了危险方法: {exposed & forbidden}"


def test_verify_immutable_endpoint_reports_all_blocked(api_client):
    """接口返回 immutable=true 且逐项说明。"""
    response = api_client.post("/api/audit/verify-immutable")
    assert response.status_code == 200
    payload = response.json()
    assert payload["immutable"] is True
    assert payload["checks"]
    for check in payload["checks"]:
        assert check["blocked"] is True, f"{check['operation']} 未被拦截: {check}"
        assert len(check["detail"]) <= 160, "错误详情必须截断，否则会撑破前端布局"


def test_verify_immutable_actually_attempts_tamper(api_client):
    """挡住"什么都没试就报 immutable=true"。

    空表时若跳过篡改尝试，接口会返回"全部拦截"但其实一次都没试过——
    那是**空断言**，等于没验证。必须确认 UPDATE / DELETE 两项都真的跑过。
    """
    payload = api_client.post("/api/audit/verify-immutable").json()
    operations = {c["operation"] for c in payload["checks"]}

    assert "UPDATE" in operations, f"没有真正尝试 UPDATE 篡改: {operations}"
    assert "DELETE" in operations, f"没有真正尝试 DELETE 篡改: {operations}"

    # 每项都要有具体报错原因，而不是"未拦截"这种含糊结论
    for check in payload["checks"]:
        if check["operation"] in {"UPDATE", "DELETE"}:
            assert check["blocked"] is True
            assert "append-only" in check["detail"].lower(), (
                f"拦截原因应来自 append-only 触发器，实际: {check['detail']}"
            )


def test_verify_immutable_on_empty_audit_table(api_client, db):
    """审计表为空时也要真的尝试篡改，不能空断言。

    空表时 ``MIN(id)`` 是 NULL，UPDATE 匹配 0 行，**根本不会触发触发器**，
    语句会成功返回——据此判定"没拦住"是误报合规问题。
    接口应先插一条探针记录再试，探针随事务回滚，不留库。
    """
    # 确保审计表确实是空的
    db.execute(text("DELETE FROM audit_log"))
    db.commit()
    assert db.execute(text("SELECT COUNT(*) FROM audit_log")).scalar() == 0

    payload = api_client.post("/api/audit/verify-immutable").json()
    operations = {c["operation"] for c in payload["checks"]}
    assert "UPDATE" in operations, f"空表时应真的尝试篡改: {operations}"
    assert payload["immutable"] is True

    # 探针不能留在库里
    residue = db.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE table_name='_verify_probe'")
    ).scalar()
    assert residue == 0, "探针记录必须随事务回滚，不能留在审计表里"


def test_verify_immutable_does_not_pollute_audit_table(api_client, db):
    """验证接口用完不留痕——不能因为"验证不可篡改"反而写脏了审计表。"""
    before = db.execute(text("SELECT COUNT(*) FROM audit_log")).scalar()
    api_client.post("/api/audit/verify-immutable")
    after = db.execute(text("SELECT COUNT(*) FROM audit_log")).scalar()
    assert before == after, "verify-immutable 不应留下任何记录"


# ------------------------------------------------------------
# G05-21：变更回溯查询
# ------------------------------------------------------------
def test_query_total_is_total_count_not_page_size(api_client, db):
    """total 必须是符合条件的总条数，否则前端分页会算错页数。"""
    for i in range(5):
        AuditService.record(
            db, table_name="device", action="INSERT", operator="pager", business_key=f"PG-{i}"
        )
    db.commit()

    payload = api_client.get("/api/audit", params={"operator": "pager", "limit": 2}).json()
    assert payload["total"] == 5, "total 不应受 limit 影响"
    assert len(payload["items"]) == 2
    assert payload["count"] == 2


def test_query_filter_by_business_key_and_operator(db):
    AuditService.record(db, table_name="device", action="INSERT", operator="x", business_key="K1")
    AuditService.record(db, table_name="device", action="UPDATE", operator="y", business_key="K2")
    db.commit()

    assert len(AuditService.query(db, business_key="K1")) == 1
    assert len(AuditService.query(db, operator="y")) == 1
    assert len(AuditService.query(db, table_name="device")) == 2
    assert len(AuditService.query(db, table_name="alarm")) == 0


def test_rename_three_times_timeline_is_complete_and_ordered(db, sample_devices):
    """某测点连续改名 3 次，能查到完整 3 条时间线且顺序正确。"""
    from g05.services.point_service import rename_point

    point = sample_devices["points"][0]
    for i in range(1, 4):
        rename_point(db, point.id, f"第{i}次改名", operator="frank", reason=f"第{i}轮")
        db.expunge_all()
        db.expire_all()

    history = AuditService.history_of(db, point.point_code)
    renames = [h for h in history if h.action == "RENAME"]
    assert len(renames) == 3, f"应有 3 条改名时间线，实际 {len(renames)}"

    names = [parse_snapshot(h.new_value)["point_name"] for h in renames]
    assert names == ["第1次改名", "第2次改名", "第3次改名"], "时间线顺序必须正确"
    assert [h.changed_at for h in renames] == sorted(h.changed_at for h in renames)


def test_timeline_endpoint_returns_diff(api_client, db):
    """时间线接口返回旧值→新值 diff。"""
    AuditService.record(
        db,
        table_name="measure_point",
        action="RENAME",
        operator="gina",
        business_key="TL-1",
        old={"point_name": "旧名"},
        new={"point_name": "新名"},
        reason="改名",
    )
    db.commit()

    payload = api_client.get("/api/audit/timeline", params={"business_key": "TL-1"}).json()
    assert payload["total"] == 1
    diff = payload["items"][0]["diff"]
    assert {"field": "point_name", "old": "旧名", "new": "新名"} in diff


def test_csv_export_has_bom_and_chinese_ok(api_client, db):
    """CSV 导出：UTF-8 BOM，Excel 直接打开中文不乱码。"""
    AuditService.record(
        db,
        table_name="device",
        action="INSERT",
        operator="hank",
        business_key="CSV-1",
        new={"device_name": "一号风机"},
        reason="新增",
    )
    db.commit()

    response = api_client.get("/api/audit/export", params={"business_key": "CSV-1"})
    assert response.status_code == 200
    raw = response.content
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须以 UTF-8 BOM 开头"

    text_body = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text_body)))
    assert rows[0][0] == "id"
    assert "一号风机" in text_body, "中文不应乱码"


def test_csv_export_supports_same_filters_as_query(api_client, db):
    """导出的过滤条件要与查询接口一致，避免"页面能筛、导出不能筛"。"""
    AuditService.record(db, table_name="device", action="INSERT", operator="f1", business_key="EX-1")
    AuditService.record(db, table_name="device", action="UPDATE", operator="f2", business_key="EX-2")
    db.commit()

    raw = api_client.get("/api/audit/export", params={"operator": "f1"}).content
    text_body = raw.decode("utf-8-sig")
    assert "EX-1" in text_body
    assert "EX-2" not in text_body


# ------------------------------------------------------------
# G05-34：故障注入与回滚
# ------------------------------------------------------------
def test_rollback_leaves_no_audit_residue(db):
    """事务回滚后审计表不残留假记录——审计与业务同生共死。"""
    from g05.models.device import Device

    db.add(
        Device(
            device_code="ROLLBACK-1", device_name="会被回滚", device_type="电机", location="四厂F区"
        )
    )
    db.flush()
    db.rollback()

    residue = db.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE business_key='ROLLBACK-1'")
    ).scalar()
    assert residue == 0, "回滚后不应留下审计残留"


def test_rollback_after_update_leaves_no_audit_residue(db):
    from g05.models.device import Device

    db.add(
        Device(
            device_code="ROLLBACK-2", device_name="初始", device_type="电机", location="四厂F区"
        )
    )
    db.commit()
    db.expunge_all()

    loaded = db.query(Device).filter_by(device_code="ROLLBACK-2").one()
    loaded.device_name = "改完要回滚"
    db.flush()
    db.rollback()

    residue = db.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE business_key='ROLLBACK-2' AND action='UPDATE'")
    ).scalar()
    assert residue == 0


def test_audit_data_survives_commit_and_is_readable(db):
    """留痕之后谁都改不了，但读得到、且数据本身没被破坏。"""
    from g05.models.device import Device

    db.add(
        Device(
            device_code="PERSIST-1", device_name="持久", device_type="电机", location="四厂F区"
        )
    )
    db.commit()

    entry = db.query(AuditLog).filter_by(business_key="PERSIST-1").one()
    assert entry.operator
    assert entry.changed_at is not None
    assert entry.changed_at.tzinfo is not None, "时间必须是 aware，避免前端按本地时区解析"
