"""pytest 公共夹具：每个测试用独立临时数据库，互不污染。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from g05.core.config import settings  # noqa: E402
from g05.core.db import get_engine, reset_engine, session_scope  # noqa: E402
from g05.core.schema import apply_triggers  # noqa: E402
from g05.models import Base  # noqa: E402


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """把数据库指向临时文件。"""
    path = tmp_path / "test.db"
    monkeypatch.setitem(settings._raw["database"]["database"], "path", str(path))  # noqa: SLF001
    reset_engine()
    yield path
    reset_engine()


@pytest.fixture()
def db(db_path):
    """建表 + 触发器，返回一个 Session 上下文（测试内自行 commit）。"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_triggers(conn)

    with session_scope() as session:
        yield session


@pytest.fixture()
def sample_devices(db):
    """两台设备 + 四个测点。"""
    from g05.models.device import Device
    from g05.models.measure_point import MeasurePoint

    fan = Device(device_code="FAN-0001", device_name="1号风机", device_type="风机", location="一厂A区")
    pump = Device(device_code="PMP-0001", device_name="1号水泵", device_type="水泵", location="一厂B区")
    db.add_all([fan, pump])
    db.flush()

    points = [
        MeasurePoint(
            device_id=fan.id, point_code="FAN0001_TEMP", point_name="轴承温度", point_type="温度", unit="℃"
        ),
        MeasurePoint(
            device_id=fan.id, point_code="FAN0001_VIB", point_name="振动", point_type="振动", unit="mm/s"
        ),
        MeasurePoint(
            device_id=pump.id, point_code="PMP0001_PRESS", point_name="出口压力", point_type="压力", unit="MPa"
        ),
    ]
    db.add_all(points)
    db.commit()
    return {"devices": [fan, pump], "points": points}


@pytest.fixture()
def sample_excel(tmp_path):
    """生成一个合法的导入工作簿。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws.append(["FAN-1001", "测试风机", "风机", "一厂A区", "运行"])
    ws.append(["PMP-1001", "测试水泵", "水泵", "二厂C区", "运行"])

    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    ws2.append(["FAN-1001", "FAN1001_TEMP", "温度", "温度", "℃"])
    ws2.append(["PMP-1001", "PMP1001_FLOW", "流量", "流量", "m³/h"])

    path = tmp_path / "valid.xlsx"
    wb.save(path)
    return path


@pytest.fixture()
def bad_excel(tmp_path):
    """生成一个含多类错误的导入工作簿（校验应打回）。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws.append(["fan-1001", "非法编号", "风机", "一厂A区", "运行"])       # 编号格式错
    ws.append(["FAN-1002", "", "外星设备", "二厂C区", "运行"])           # 名称空 + 类型不在枚举
    ws.append(["FAN-1002", "重复编号", "风机", "一厂A区", "运行"])       # 编号重复

    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    ws2.append(["NOT-EXIST", "P_001", "温度", "温度", "℃"])              # 外键不存在

    path = tmp_path / "bad.xlsx"
    wb.save(path)
    return path
