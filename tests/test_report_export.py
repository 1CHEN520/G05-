"""校验报告导出测试（G05-32）：Excel 中文不乱码、行号准确、JSON 与接口一致。"""

from __future__ import annotations

import json

from openpyxl import load_workbook

from g05.importers.validator import RuleEngine
from g05.reports import export_reports_excel, export_reports_json

_SUMMARY_SHEET = "汇总"
_DETAIL_SHEET = "问题明细"


def _engine() -> RuleEngine:
    return RuleEngine()


def _sample_reports() -> list:
    """构造两份报告：device 合规 1 行；measure_point 有致命 + 普通问题。"""
    device_rows = [
        {
            "device_code": "FAN-0001",
            "device_name": "1号风机",
            "device_type": "风机",
            "location": "一厂A区",
            "status": "运行",
        }
    ]
    measure_rows = [
        {
            "device_code": "GHOST-9999",  # 外键悬空（致命）
            "point_code": "P_001",
            "point_name": "轴承温度",
            "point_type": "外星类型",  # 枚举违规（普通）
            "unit": "℃",
        }
    ]
    engine = _engine()
    fk = {"device.device_code": {"FAN-0001"}}
    return [
        engine.validate("device", device_rows),
        engine.validate("measure_point", measure_rows, foreign_keys=fk),
    ]


def test_excel_export_creates_two_sheets_with_chinese(tmp_path):
    reports = _sample_reports()
    out = export_reports_excel(reports, tmp_path / "打回单.xlsx")

    assert out.exists()
    wb = load_workbook(out)
    assert wb.sheetnames == [_SUMMARY_SHEET, _DETAIL_SHEET]


def test_excel_summary_contains_scores_and_fatal_count(tmp_path):
    reports = _sample_reports()
    out = export_reports_excel(reports, tmp_path / "打回单.xlsx")
    ws = load_workbook(out)[_SUMMARY_SHEET]

    headers = [c.value for c in ws[1]]
    rows = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True)}

    assert "致命错误数" in headers and "是否放行" in headers and "总分" in headers
    device_row = rows["device"]
    assert device_row[headers.index("是否放行")] == "是"
    assert device_row[headers.index("致命错误数")] == 0

    measure_row = rows["measure_point"]
    assert measure_row[headers.index("是否放行")] == "否"
    assert measure_row[headers.index("致命错误数")] == 1  # foreign_key 悬空
    assert measure_row[headers.index("总分")] is not None


def test_excel_detail_rows_use_excel_row_numbers_and_original_values(tmp_path):
    """明细页必须有：行号（Excel 真实行号）、列、规则、说明、原值，中文可读。"""
    reports = _sample_reports()
    out = export_reports_excel(reports, tmp_path / "打回单.xlsx")
    ws = load_workbook(out)[_DETAIL_SHEET]

    headers = [c.value for c in ws[1]]
    assert headers == ["Sheet", "行号", "列", "规则", "说明", "原值"]

    fk_issues = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[3] == "foreign_key"]
    assert fk_issues, "外键问题应出现在明细页"
    row = fk_issues[0]
    assert row[0] == "measure_point"
    assert row[1] == 2  # 表头第 1 行，数据第 1 条 = Excel 第 2 行
    assert row[2] == "device_code"
    assert row[4] == "device_code 引用的 device.device_code 不存在: GHOST-9999"  # 说明为中文
    assert row[5] == "GHOST-9999"  # 原值，未被清洗


def test_json_export_matches_api_contract(tmp_path):
    """JSON 内容必须与接口返回（to_dict）完全一致 —— 契约一致性验收。"""
    reports = _sample_reports()
    out = export_reports_json(reports, tmp_path / "打回单.json")

    raw = out.read_text(encoding="utf-8-sig")
    payload = json.loads(raw)

    assert payload["ok"] is False  # measure_point 未通过
    assert [d["sheet"] for d in payload["reports"]] == ["device", "measure_point"]
    for report, data in zip(reports, payload["reports"]):
        assert data == report.to_dict()  # 与接口返回逐字段一致


def test_export_accepts_dict_reports(tmp_path):
    """接口侧拿到的是 to_dict() 结果，也应能直接导出。"""
    reports = [r.to_dict() for r in _sample_reports()]
    out = export_reports_excel(reports, tmp_path / "打回单.xlsx")
    ws = load_workbook(out)[_SUMMARY_SHEET]
    assert ws.max_row == 3  # 表头 + 2 个 sheet


def test_excel_parent_dir_auto_created(tmp_path):
    out = export_reports_excel(_sample_reports(), tmp_path / "a" / "b" / "打回单.xlsx")
    assert out.exists()
