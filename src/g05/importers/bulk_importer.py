"""Excel 批量导入（整批原子）。

流程：
1. 读取工作簿 → 逐 sheet 解析为 dict 列表
2. 用 RuleEngine 校验（规则来自 YAML）
3. 校验不通过 → 整批不写库，返回打回单
4. 校验通过 → bulk insert（分片提交），失败则整批回滚

性能（硬指标 万行 <30 秒）：
- ``session.bulk_insert_mappings`` 绕过 ORM 逐行开销
- 分片 ``chunk_size`` 提交，平衡内存与事务粒度
- 关闭 ``expire_on_commit``，避免提交后重新查询
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from g05.audit.service import AuditService
from g05.core.config import settings
from g05.core.logging import get_logger, log_event
from g05.importers.validator import RuleEngine, ValidationReport
from g05.models.device import Device
from g05.models.measure_point import MeasurePoint

log = get_logger("importers.bulk")


@dataclass
class ImportResult:
    ok: bool
    file: str
    inserted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    reports: list[ValidationReport] = field(default_factory=list)
    elapsed_ms: float = 0.0
    message: str = ""
    rolled_back: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "file": self.file,
            "inserted": self.inserted,
            "updated": self.updated,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "message": self.message,
            "rolled_back": self.rolled_back,
            "reports": [r.to_dict() for r in self.reports],
        }


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def read_sheet(path: Path, sheet_name: str) -> list[dict[str, Any]]:
    """读取指定 sheet 为 dict 列表，首行为表头。"""
    wb = load_workbook(filename=path, read_only=True, data_only=True)
    try:
        if sheet_name not in wb.sheetnames:
            raise KeyError(f"工作簿缺少 sheet「{sheet_name}」，实际有 {wb.sheetnames}")
        ws = wb[sheet_name]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = next(rows_iter)
        except StopIteration:
            return []

        columns = [_normalize(h) for h in header if h is not None]
        records: list[dict[str, Any]] = []
        for raw in rows_iter:
            if raw is None or all(cell is None or cell == "" for cell in raw):
                continue  # 跳过空行
            row = {}
            for idx, col in enumerate(columns):
                row[col] = _normalize(raw[idx]) if idx < len(raw) else None
            records.append(row)
        return records
    finally:
        wb.close()


def _existing_keys(session: Session, model, key_attr: str) -> dict[str, int]:  # noqa: ANN001
    attr = getattr(model, key_attr)
    rows = session.execute(select(attr, model.id)).all()
    return {k: v for k, v in rows}


def import_workbook(
    session: Session,
    path: str | Path,
    *,
    operator: str = "system",
    sheets: list[str] | None = None,
) -> ImportResult:
    """导入设备台账工作簿。

    顺序强制为 device -> measure_point（后者依赖前者的外键）。
    """
    import time

    started = time.perf_counter()
    path = Path(path)
    result = ImportResult(ok=False, file=str(path))

    if not path.exists():
        result.message = f"文件不存在: {path}"
        return result

    allowed_ext = settings.get("app.import.allowed_ext", [".xlsx"])
    if path.suffix.lower() not in allowed_ext:
        result.message = f"不支持的文件类型 {path.suffix}，允许 {allowed_ext}"
        return result

    engine = RuleEngine()
    max_rows = settings.get("app.import.max_rows", 200000)
    chunk_size = settings.get("app.import.chunk_size", 1000)
    rollback_on_error = settings.get("app.import.rollback_on_error", True)

    target_sheets = sheets or ["device", "measure_point"]

    # ---------- 1. 解析 + 校验 ----------
    parsed: dict[str, list[dict]] = {}
    for sheet in target_sheets:
        try:
            rows = read_sheet(path, engine.sheet_rule(sheet)["sheet_name"])
        except KeyError as exc:
            result.message = str(exc)
            return result
        if len(rows) > max_rows:
            result.message = f"sheet {sheet} 行数 {len(rows)} 超过上限 {max_rows}"
            return result
        parsed[sheet] = rows

    # 外键上下文：导入测点前，需要知道已存在的设备编号
    existing_devices = set(_existing_keys(session, Device, "device_code").keys())
    incoming_devices = {
        str(r.get("device_code")).strip()
        for r in parsed.get("device", [])
        if r.get("device_code")
    }
    fk_context = {"device.device_code": existing_devices | incoming_devices}

    for sheet, rows in parsed.items():
        report = engine.validate(sheet, rows, foreign_keys=fk_context)
        result.reports.append(report)

    if any(not r.passed for r in result.reports):
        result.message = "校验未通过，整批未写入（打回单见 reports）"
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        log_event(
            log,
            "import.rejected",
            file=path.name,
            sheets={r.sheet: r.total_score for r in result.reports},
        )
        return result

    # ---------- 2. 写入（单事务，异常整批回滚）----------
    allow_update = settings.get("app.import.allow_update", False)
    try:
        device_map = _existing_keys(session, Device, "device_code")
        device_rows = parsed.get("device", [])
        device_payload: list[dict] = []
        for row in device_rows:
            code = str(row["device_code"]).strip()
            if code in device_map:
                if allow_update:
                    result.updated["device"] = result.updated.get("device", 0) + 1
                continue
            device_payload.append(
                {
                    "device_code": code,
                    "device_name": row["device_name"],
                    "device_type": row["device_type"],
                    "location": row["location"],
                    "status": row.get("status") or "运行",
                    "is_deleted": False,
                    "version": 1,
                }
            )

        for start in range(0, len(device_payload), chunk_size):
            session.bulk_insert_mappings(Device, device_payload[start : start + chunk_size])
            session.flush()
        result.inserted["device"] = len(device_payload)

        # 设备写完后重新取 id 映射
        device_map = _existing_keys(session, Device, "device_code")

        point_rows = parsed.get("measure_point", [])
        point_map = _existing_keys(session, MeasurePoint, "point_code")
        point_payload: list[dict] = []
        for row in point_rows:
            code = str(row["point_code"]).strip()
            device_code = str(row["device_code"]).strip()
            if code in point_map:
                if allow_update:
                    result.updated["measure_point"] = result.updated.get("measure_point", 0) + 1
                continue
            device_id = device_map.get(device_code)
            if device_id is None:
                raise ValueError(f"设备编号 {device_code} 未找到（外键约束）")
            point_payload.append(
                {
                    "device_id": device_id,
                    "point_code": code,
                    "point_name": row["point_name"],
                    "point_type": row["point_type"],
                    "unit": row["unit"],
                    "is_deleted": False,
                    "version": 1,
                }
            )

        for start in range(0, len(point_payload), chunk_size):
            session.bulk_insert_mappings(MeasurePoint, point_payload[start : start + chunk_size])
            session.flush()
        result.inserted["measure_point"] = len(point_payload)

        # ---------- 3. 审计 ----------
        AuditService.record(
            session,
            table_name="import",
            action="INSERT",
            operator=operator,
            business_key=path.name,
            new={
                "file": path.name,
                "inserted": result.inserted,
                "updated": result.updated,
                "scores": {r.sheet: r.total_score for r in result.reports},
            },
            reason="Excel 批量导入",
        )

        session.commit()
        result.ok = True
        result.message = "导入成功"
        log_event(log, "import.committed", file=path.name, inserted=result.inserted)

    except Exception as exc:  # noqa: BLE001
        session.rollback()
        result.rolled_back = rollback_on_error
        result.ok = False
        result.message = f"写入失败已整批回滚: {exc}"
        log.error("import.failed", extra={"extra_fields": {"file": path.name, "error": str(exc)}})

    result.elapsed_ms = (time.perf_counter() - started) * 1000
    return result


def build_rejection_note(result: ImportResult) -> str:
    """生成可读的打回单（Markdown），带坏行号定位。"""
    lines = [f"# 导入打回单 — {result.file}", ""]
    for report in result.reports:
        lines.append(f"## {report.sheet}")
        lines.append(report.summary_line())
        lines.append("")
        if report.issues:
            lines.append("| Excel行号 | 列 | 规则 | 说明 | 值 |")
            lines.append("|---|---|---|---|---|")
            for issue in report.issues[:200]:
                d = issue.to_dict()
                lines.append(
                    f"| {d['row']} | {d['column']} | {d['rule']} | {d['message']} | {d['value']} |"
                )
            lines.append("")
    return "\n".join(lines)
