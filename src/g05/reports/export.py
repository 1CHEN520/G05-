"""校验报告导出（G05-32）：Excel + JSON 双格式。

设计要点：
- Excel 用 openpyxl 原生生成（xlsx 内部即 UTF-8，中文双击打开不乱码）。
  不用 CSV —— 中文 Windows 上 CSV 默认 GBK 编码，双击打开就是乱码，这是历史坑
- JSON 与接口返回完全一致：直接消费 ``ValidationReport.to_dict()`` 的契约结构，
  保证"报告内容与接口返回一致"这条验收
- 导出是纯只读操作：只读取 report 对象，不修改任何原始数据

用法（C1 / API / 运维脚本都可调用）::

    reports = [engine.validate("device", rows)]
    export_reports_excel(reports, "reports/打回单.xlsx")
    export_reports_json(reports, "reports/打回单.json")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from g05.core.logging import get_logger
from g05.importers.validator import ValidationReport

log = get_logger("reports.export")

# 既能吃 ValidationReport 对象，也能吃 API 已经序列化好的 dict（保持同一契约）
ReportLike = ValidationReport | dict[str, Any]

# 明细页表头：行号、列、规则、说明、原值 —— 与接口 issues 字段一一对应
_DETAIL_HEADERS = ["行号", "列", "规则", "说明", "原值"]

# 汇总页表头：各 sheet 得分、通过率、致命错误数
_SUMMARY_HEADERS = [
    "Sheet",
    "总行数",
    "有效行数",
    "问题行数",
    "问题数",
    "致命错误数",
    "完整性",
    "合规性",
    "唯一性",
    "总分",
    "阈值",
    "通过率",
    "是否放行",
]

_HEADER_FILL = PatternFill("solid", fgColor="4472C4")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_SHEET_NAME_SUMMARY = "汇总"
_SHEET_NAME_DETAIL = "问题明细"


def _normalize(report: ReportLike) -> dict[str, Any]:
    """把 ValidationReport 或 dict 统一成契约 dict（与接口返回同构）。"""
    if isinstance(report, dict):
        return report
    return report.to_dict()


def _detail_rows(data: dict[str, Any]) -> list[list[Any]]:
    """从契约 dict 抽出明细行：行号 / 列 / 规则 / 说明 / 原值。"""
    rows: list[list[Any]] = []
    for issue in data.get("issues", []):
        rows.append(
            [
                issue.get("row"),
                issue.get("column"),
                issue.get("rule"),
                issue.get("message"),
                issue.get("value"),
            ]
        )
    return rows


def _fill_sheet_with_table(ws: Any, headers: list[str], rows: list[list[Any]]) -> None:
    """写入表头 + 数据行，并做基础美化（表头底色、自动列宽、居中）。"""
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row in rows:
        ws.append(row)

    # 自动列宽：按每列最长可见字符估算（中文按 2 个宽度算）
    for col_idx in range(1, len(headers) + 1):
        max_width = len(headers[col_idx - 1])
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, values_only=True):
            for value in row:
                text = "" if value is None else str(value)
                width = sum(2 if ord(ch) > 127 else 1 for ch in text)
                max_width = max(max_width, width)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_width + 2, 60)
    ws.freeze_panes = "A2"


def export_reports_excel(reports: Sequence[ReportLike], path: str | Path) -> Path:
    """导出校验报告 Excel。

    结构：
    - 「汇总」页：各 sheet 得分、通过率、致命错误数、是否放行
    - 「问题明细」页：行号、列、规则、说明、原值（所有 sheet 的问题合并，带 sheet 列）

    Args:
        reports: ValidationReport 列表（或 to_dict() 结果列表）
        path: 目标 .xlsx 路径，父目录不存在时自动创建

    Returns:
        实际写出的文件 Path
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data_list = [_normalize(r) for r in reports]

    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = _SHEET_NAME_SUMMARY

    summary_rows: list[list[Any]] = []
    detail_rows: list[list[Any]] = []
    for data in data_list:
        total_rows = int(data.get("total_rows") or 0)
        valid_rows = int(data.get("valid_rows") or 0)
        pass_rate = (valid_rows / total_rows * 100) if total_rows else 0.0
        scores = data.get("scores") or {}
        summary_rows.append(
            [
                data.get("sheet"),
                total_rows,
                valid_rows,
                int(data.get("invalid_rows") or 0),
                int(data.get("issue_count") or 0),
                int(data.get("fatal_count") or 0),
                scores.get("completeness"),
                scores.get("compliance"),
                scores.get("uniqueness"),
                data.get("total_score"),
                data.get("threshold"),
                round(pass_rate, 2),
                "是" if data.get("passed") else "否",
            ]
        )
        for row in _detail_rows(data):
            # 明细第一列补 sheet 名，方便在合并页里定位来源
            detail_rows.append([data.get("sheet"), *row])

    _fill_sheet_with_table(ws_summary, _SUMMARY_HEADERS, summary_rows)
    ws_detail = wb.create_sheet(_SHEET_NAME_DETAIL)
    _fill_sheet_with_table(ws_detail, ["Sheet", *_DETAIL_HEADERS], detail_rows)

    wb.save(out_path)
    log.info(
        "report.export.excel",
        extra={
            "extra_fields": {
                "path": str(out_path),
                "sheets": len(data_list),
                "issues": len(detail_rows),
            }
        },
    )
    return out_path


def export_reports_json(reports: Sequence[ReportLike], path: str | Path) -> Path:
    """导出校验报告 JSON。

    内容与 API 返回同构：``{"ok": ..., "reports": [每个 sheet 的契约 dict]}``，
    供接口透传与自动化脚本消费。

    注意用 UTF-8 带 BOM（utf-8-sig）写出，Windows 记事本/Excel 直接打开不乱码。
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data_list = [_normalize(r) for r in reports]
    payload = {
        "ok": all(bool(d.get("passed")) for d in data_list) if data_list else False,
        "reports": data_list,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )
    log.info(
        "report.export.json",
        extra={"extra_fields": {"path": str(out_path), "sheets": len(data_list)}},
    )
    return out_path


def export_reports(
    reports: Sequence[ReportLike],
    excel_path: str | Path | None = None,
    json_path: str | Path | None = None,
) -> dict[str, Path]:
    """便捷入口：一次调用同时导出 Excel 与 JSON（可任选其一）。"""
    written: dict[str, Path] = {}
    if excel_path is not None:
        written["excel"] = export_reports_excel(reports, excel_path)
    if json_path is not None:
        written["json"] = export_reports_json(reports, json_path)
    if not written:
        raise ValueError("excel_path 与 json_path 至少提供一个")
    return written
