"""审计测试：写入、查询、不可篡改、历史时间线。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from g05.audit.service import AuditService
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
