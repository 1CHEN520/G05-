"""ORM 模型与约束测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from g05.models.device import Device
from g05.models.measure_point import MeasurePoint


def test_device_code_unique(db):
    db.add(Device(device_code="FAN-0001", device_name="a", device_type="风机", location="x"))
    db.commit()

    db.add(Device(device_code="FAN-0001", device_name="b", device_type="风机", location="y"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_orphan_point_rejected(db, sample_devices):
    """孤儿记录：引用不存在的设备，必须被数据库拒绝。"""
    orphan = MeasurePoint(
        device_id=99999, point_code="ORPHAN_01", point_name="孤儿", point_type="温度", unit="℃"
    )
    db.add(orphan)
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    db.rollback()
    assert "FOREIGN KEY" in str(exc.value).upper()


def test_foreign_keys_pragma_on(db):
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_wal_mode_enabled(db):
    mode = db.execute(text("PRAGMA journal_mode")).scalar()
    assert str(mode).lower() == "wal"


def test_soft_delete_keeps_row(db, sample_devices):
    point = sample_devices["points"][0]
    point.is_deleted = True
    db.commit()

    still_there = db.get(MeasurePoint, point.id)
    assert still_there is not None
    assert still_there.is_deleted is True


def test_version_increment_on_update(db, sample_devices):
    point = sample_devices["points"][0]
    assert point.version == 1
    point.point_name = "新名字"
    point.version += 1
    db.commit()
    assert db.get(MeasurePoint, point.id).version == 2


def test_physical_delete_device_with_points_blocked(db, sample_devices):
    """资产保护：有测点的设备不允许物理删除（外键 RESTRICT），业务层用软删。"""
    device = sample_devices["devices"][0]
    db.delete(device)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_device_without_points_can_be_deleted(db):
    from g05.models.device import Device as D

    lonely = D(device_code="SOLO-0001", device_name="孤设备", device_type="电机", location="三厂E区")
    db.add(lonely)
    db.commit()

    db.delete(lonely)
    db.commit()
    assert db.get(D, lonely.id) is None
