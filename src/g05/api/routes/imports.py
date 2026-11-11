"""导入 API：Excel 上传 + 校验报告 + 打回单。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from g05.api.deps import get_db, get_operator
from g05.core.config import settings
from g05.core.logging import get_logger
from g05.importers.bulk_importer import import_workbook
from g05.schemas.dto import ImportSummary

router = APIRouter(prefix="/import", tags=["导入"])
log = get_logger("api.import")

# 单块拷贝大小：流式写盘 + 逐块计数，超限立刻中断，避免超大文件先占满磁盘
UPLOAD_CHUNK = 1024 * 1024


def _max_upload_mb() -> int:
    """上传大小上限（MB）：从 config/app.yaml 读，可配置覆盖，不在代码里写死。"""
    return int(settings.get("app.import.max_upload_mb", 50))


def _save_upload_capped(file: UploadFile, suffix: str) -> Path:
    """把上传内容流式落盘，边写边计数；超过上限立即删除并抛 413。

    相比"先整体写完再 stat 大小"，超大文件不会再把临时目录写满。
    """
    limit = _max_upload_mb() * UPLOAD_CHUNK
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        total = 0
        try:
            while chunk := file.file.read(UPLOAD_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(413, f"文件超过上限 {_max_upload_mb()}MB")
                tmp.write(chunk)
        except HTTPException:
            tmp_path.unlink(missing_ok=True)
            raise
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    return tmp_path


@router.post("/excel", response_model=ImportSummary, summary="上传 Excel 批量导入")
async def upload_excel(
    file: UploadFile = File(..., description="设备台账工作簿（含「设备」「测点」两个 sheet）"),
    db: Session = Depends(get_db),
    operator: str = Depends(get_operator),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(400, f"仅支持 .xlsx/.xlsm，收到 {suffix or '未知'}")

    tmp_path = _save_upload_capped(file, suffix)
    try:
        result = import_workbook(db, tmp_path, operator=operator)
        payload = result.to_dict()
        payload["file"] = file.filename or "uploaded.xlsx"
        return ImportSummary(**payload)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/validate", summary="仅校验不写入（预检）")
async def validate_only(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """预检模式：跑完整校验流程，但用 savepoint 回滚，不产生任何写入。"""
    from sqlalchemy import select

    from g05.importers.bulk_importer import read_sheet
    from g05.importers.validator import RuleEngine
    from g05.models.device import Device

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(400, "仅支持 .xlsx/.xlsm")

    tmp_path = _save_upload_capped(file, suffix)
    try:
        engine = RuleEngine()
        existing = {
            r for r in db.execute(select(Device.device_code)).scalars()
        }
        reports = []
        # 设备 sheet 只读一次：既用于外键上下文，也用于校验，省一次全表解析
        device_rows = read_sheet(tmp_path, engine.sheet_rule("device")["sheet_name"])
        incoming = {str(r.get("device_code", "")).strip() for r in device_rows if r.get("device_code")}
        fk_ctx = {"device.device_code": existing | incoming}
        for sheet in ("device", "measure_point"):
            rows = device_rows if sheet == "device" else read_sheet(
                tmp_path, engine.sheet_rule(sheet)["sheet_name"]
            )
            reports.append(engine.validate(sheet, rows, foreign_keys=fk_ctx).to_dict())
        return {"ok": all(r["passed"] for r in reports), "reports": reports}
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/template", summary="下载导入模板说明")
def template_doc():
    content = """# G05 设备台账导入模板说明

工作簿需包含两个 sheet：

## sheet「设备」
| 列名 | 必填 | 格式 | 示例 |
|---|---|---|---|
| device_code | 是 | 2-4位大写字母 + '-' + 3-6位数字，唯一 | FAN-0012 |
| device_name | 是 | 1-64 字符 | 1号引风机 |
| device_type | 是 | 枚举：风机/水泵/压缩机/电机/减速机/换热器/锅炉/空压机/输送机/变压器/变频器/冷却塔 | 风机 |
| location | 是 | 1-128 字符 | 一厂A区 |
| status | 否 | 运行/停机/检修/报废，默认运行 | 运行 |

## sheet「测点」
| 列名 | 必填 | 格式 | 示例 |
|---|---|---|---|
| device_code | 是 | 必须已存在于设备表或同批导入 | FAN-0012 |
| point_code | 是 | 2-48 位大写字母/数字/下划线，唯一 | FAN0012_TEMP |
| point_name | 是 | 1-64 字符 | 轴承温度 |
| point_type | 是 | 温度/压力/振动/转速/电流/电压/流量/液位/位移/噪声/湿度/功率 | 温度 |
| unit | 是 | 1-16 字符 | ℃ |

校验规则来源：config/rules.yaml（改规则无需改代码）
质量评分：完整性40% + 合规性35% + 唯一性25%，≥85 放行
"""
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")
