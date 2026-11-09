"""校验报告导出模块（G05-32）。

对外只暴露导出函数，契约结构与 ``ValidationReport.to_dict()`` 一致：
- Excel（openpyxl 原生，中文不乱码）：汇总页 + 问题明细页
- JSON（UTF-8 BOM）：与接口返回同构，供自动化消费
"""

from __future__ import annotations

from g05.reports.export import export_reports, export_reports_excel, export_reports_json

__all__ = ["export_reports", "export_reports_excel", "export_reports_json"]
