"""导入流程测试：整批原子、回滚、性能、打回单。"""

from __future__ import annotations

import time

from g05.importers.bulk_importer import build_rejection_note, import_workbook, read_sheet
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
