"""导入流程测试：整批原子、回滚、性能、打回单。"""

from __future__ import annotations

import time

from g05.importers.bulk_importer import (
    build_rejection_note,
    import_workbook,
    read_sheet,
    read_sheet_with_rows,
)
from g05.models.device import Device
from g05.models.measure_point import MeasurePoint


def test_read_sheet_parses_headers(sample_excel):
    rows = read_sheet(sample_excel, "设备")
    assert len(rows) == 2
    assert rows[0]["device_code"] == "FAN-1001"


def test_valid_excel_imported(db, sample_excel):
    result = import_workbook(db, sample_excel, operator="tester")
    assert result.ok is True
    assert result.inserted["device"] == 2
    assert result.inserted["measure_point"] == 2
    assert db.query(Device).count() == 2
    assert db.query(MeasurePoint).count() == 2


def test_bad_excel_rejected_and_nothing_written(db, bad_excel):
    """校验不通过 → 整批不写库。"""
    result = import_workbook(db, bad_excel, operator="tester")
    assert result.ok is False
    assert db.query(Device).count() == 0
    assert db.query(MeasurePoint).count() == 0
    assert "整批未写入" in result.message


def test_rejection_note_contains_row_numbers(db, bad_excel):
    result = import_workbook(db, bad_excel, operator="tester")
    note = build_rejection_note(result)
    assert "打回单" in note
    assert "Excel行号" in note


def test_import_reports_scores(db, sample_excel):
    result = import_workbook(db, sample_excel, operator="tester")
    assert result.reports
    report = result.reports[0]
    assert report.total_score >= 85
    assert report.passed is True


def test_import_is_idempotent_on_duplicate(db, sample_excel):
    first = import_workbook(db, sample_excel, operator="t")
    second = import_workbook(db, sample_excel, operator="t")
    assert first.inserted["device"] == 2
    assert second.inserted["device"] == 0  # 已存在，跳过
    assert db.query(Device).count() == 2


def test_import_writes_audit(db, sample_excel):
    from g05.audit.service import AuditService

    import_workbook(db, sample_excel, operator="audit-check")
    entries = AuditService.query(db, table_name="import", operator="audit-check")
    assert len(entries) >= 1


def test_import_missing_file(db, tmp_path):
    result = import_workbook(db, tmp_path / "nope.xlsx", operator="t")
    assert result.ok is False
    assert "不存在" in result.message


def test_import_unsupported_extension(db, tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,2", encoding="utf-8")
    result = import_workbook(db, path, operator="t")
    assert result.ok is False
    assert "不支持" in result.message


def test_import_10k_rows_under_30s(db, tmp_path):
    """硬指标：万行导入 <30 秒。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    for i in range(5000):
        ws.append([f"FAN-{i:05d}", f"{i}号风机", "风机", "一厂A区", "运行"])

    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    for i in range(5000):
        ws2.append([f"FAN-{i:05d}", f"FAN{i:05d}_TEMP", "温度", "温度", "℃"])

    path = tmp_path / "bulk.xlsx"
    wb.save(path)

    started = time.perf_counter()
    result = import_workbook(db, path, operator="perf")
    elapsed = time.perf_counter() - started

    assert result.ok is True
    assert result.inserted["device"] == 5000
    assert result.inserted["measure_point"] == 5000
    assert elapsed < 30, f"万行导入耗时 {elapsed:.1f}s，超过 30 秒硬指标"


# ------------------------------------------------------------
# 解析层防御用例（G05-15 增强）
# ------------------------------------------------------------

def test_read_sheet_tolerates_header_spaces(tmp_path):
    """表头带半角/全角空格也能与规则列名对上。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    # " device_code " 半角空格、"device_name　" 全角空格（\\u3000）
    ws.append([" device_code ", "device_name　", "device_type", "location", "status"])
    ws.append(["FAN-2001", "测试风机", "风机", "一厂A区", "运行"])
    path = tmp_path / "space_header.xlsx"
    wb.save(path)

    rows = read_sheet(path, "设备")
    assert len(rows) == 1
    assert rows[0]["device_code"] == "FAN-2001"
    assert rows[0]["device_name"] == "测试风机"


def test_read_sheet_skips_none_header_without_misalignment(tmp_path):
    """表头行中间出现空列时，其余列按真实下标映射，不错位。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    # 第 2 列表头缺失：device_name 列无表头，后面列必须仍然各归各位
    ws.append(["device_code", None, "device_type", "location", "status"])
    ws.append(["FAN-2002", "无表头的名字", "风机", "一厂A区", "运行"])
    path = tmp_path / "none_header.xlsx"
    wb.save(path)

    rows = read_sheet(path, "设备")
    assert len(rows) == 1
    row = rows[0]
    assert row["device_code"] == "FAN-2002"       # 第 1 列
    assert "device_name" not in row               # 无表头的列不进数据
    assert row["device_type"] == "风机"            # 第 3 列，未左移
    assert row["location"] == "一厂A区"            # 第 4 列，未左移
    assert row["status"] == "运行"                 # 第 5 列，未左移


def test_read_sheet_with_rows_returns_real_excel_rows(tmp_path):
    """带行号解析：中间有空行时，返回的是 Excel 真实行号而非列表下标。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws.append(["FAN-3001", "第2行", "风机", "一厂A区", "运行"])
    ws.append([None, None, None, None, None])                  # 第 3 行：空行
    ws.append(["FAN-3002", "第4行", "风机", "一厂A区", "运行"])
    path = tmp_path / "gap_rows.xlsx"
    wb.save(path)

    rows, row_numbers = read_sheet_with_rows(path, "设备")
    assert len(rows) == 2
    assert row_numbers == [2, 4]                  # 空行占掉第 3 行，第 4 行仍是 4
    assert rows[1]["device_name"] == "第4行"


def test_empty_workbook_rejected(db, tmp_path):
    """只有表头没有数据的空工作簿：明确打回，不写库。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    path = tmp_path / "empty.xlsx"
    wb.save(path)

    result = import_workbook(db, path, operator="tester")
    assert result.ok is False
    assert "无有效数据" in result.message
    assert db.query(Device).count() == 0


def test_fullwidth_space_in_cell_is_trimmed(db, tmp_path):
    """单元格值首尾带全角空格：清洗后入库，内部全角空格保留。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws.append(["FAN-4001", "　测试风机　", "风机", " 一厂　A区 ", "运行"])
    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    ws2.append(["FAN-4001", "FAN4001_TEMP", "温度", "温度", "℃"])
    path = tmp_path / "fullwidth.xlsx"
    wb.save(path)

    result = import_workbook(db, path, operator="tester")
    assert result.ok is True
    device = db.query(Device).filter_by(device_code="FAN-4001").one()
    assert device.device_name == "测试风机"          # 首尾全角空格被清掉
    assert device.location == "一厂　A区"            # 内部全角空格保留（不篡改数据）
