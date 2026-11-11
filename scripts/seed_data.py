"""生成演示数据：设备、测点、血缘图、样例 Excel。

用法::

    python scripts/seed_data.py              # 灌入演示数据
    python scripts/seed_data.py --reset      # 清空业务表后重灌
    python scripts/seed_data.py --excel-only # 只生成 Excel 样例，不写库
    python scripts/seed_data.py --excel-only --per-type 2000
                                             # 生成 2.4 万行设备的压测样例
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import delete  # noqa: E402

from g05.audit.service import AuditService, audit_as  # noqa: E402
from g05.core.config import PROJECT_ROOT  # noqa: E402
from g05.core.db import session_scope  # noqa: E402
from g05.core.logging import get_logger, log_event  # noqa: E402
from g05.lineage.impact import register_edge, register_node  # noqa: E402
from g05.models.alarm import Alarm  # noqa: E402
from g05.models.device import Device  # noqa: E402
from g05.models.lineage import LineageEdge, LineageNode  # noqa: E402
from g05.models.measure_point import MeasurePoint  # noqa: E402
from g05.models.record import Record  # noqa: E402

log = get_logger("scripts.seed")

random.seed(20260501)  # 可复现

DEVICE_TYPES = {
    "风机": ("FAN", ["振动", "温度", "转速"]),
    "水泵": ("PMP", ["压力", "流量", "电流"]),
    "压缩机": ("CMP", ["压力", "温度", "振动"]),
    "电机": ("MTR", ["电流", "温度", "转速"]),
    "减速机": ("RED", ["振动", "温度", "噪声"]),
    "换热器": ("HEX", ["温度", "压力", "流量"]),
    "锅炉": ("BLR", ["温度", "压力", "液位"]),
    "空压机": ("AIR", ["压力", "温度", "功率"]),
    "输送机": ("CNV", ["转速", "电流", "振动"]),
    "变压器": ("TRF", ["电压", "电流", "温度"]),
    "变频器": ("VFD", ["电流", "电压", "功率"]),
    "冷却塔": ("CTW", ["温度", "湿度", "流量"]),
}

UNITS = {
    "温度": "℃", "压力": "MPa", "振动": "mm/s", "转速": "rpm", "电流": "A",
    "电压": "V", "流量": "m³/h", "液位": "m", "位移": "mm", "噪声": "dB",
    "湿度": "%RH", "功率": "kW",
}

# 测点编码后缀：必须满足 rules.yaml 中 ^[A-Z0-9_]{2,48}$ 的约束
POINT_CODE = {
    "温度": "TEMP", "压力": "PRESS", "振动": "VIB", "转速": "RPM", "电流": "CUR",
    "电压": "VOLT", "流量": "FLOW", "液位": "LEVEL", "位移": "DISP", "噪声": "NOISE",
    "湿度": "HUM", "功率": "POW",
}

AREAS = ["一厂A区", "一厂B区", "二厂C区", "二厂D区", "三厂E区"]
STATUS = ["运行", "运行", "运行", "停机", "检修"]

# 下游消费方（血缘图的下游节点）
REPORTS = [
    ("RPT_OEE_DAILY", "OEE 日报", "生产运营部"),
    ("RPT_QUALITY_WEEKLY", "质量周报", "质量管理部"),
    ("RPT_ENERGY_MONTHLY", "能耗月报", "能源管理部"),
    ("RPT_ALARM_SUMMARY", "告警汇总看板", "设备管理部"),
    ("RPT_MAINT_PLAN", "检修计划表", "设备管理部"),
]
TASKS = [
    ("TASK_FEATURE_DAILY", "日特征计算", "数据组"),
    ("TASK_DRIFT_CHECK", "特征漂移检测", "数据组"),
    ("TASK_HEALTH_SCORE", "健康度评分", "算法组"),
]
APIS = [
    ("API_KPI_QUERY", "KPI 查询接口", "平台组"),
    ("API_ASSET_CATALOG", "资产目录接口", "平台组"),
]


def build_excel(path: Path, device_rows: list[dict], point_rows: list[dict]) -> None:
    """生成样例导入工作簿。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    headers = ["device_code", "device_name", "device_type", "location", "status"]
    ws.append(headers)
    for row in device_rows:
        ws.append([row.get(h) for h in headers])

    ws2 = wb.create_sheet("测点")
    headers2 = ["device_code", "point_code", "point_name", "point_type", "unit"]
    ws2.append(headers2)
    for row in point_rows:
        ws2.append([row.get(h) for h in headers2])

    header_fill = PatternFill("solid", fgColor="EFF4FF")
    for sheet in (ws, ws2):
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="1D4ED8")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            width = max(len(str(c.value or "")) for c in column) + 4
            sheet.column_dimensions[column[0].column_letter].width = min(width, 32)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    log_event(log, "excel.generated", path=str(path), devices=len(device_rows), points=len(point_rows))


def generate_dataset(per_type: int = 5) -> tuple[list[dict], list[dict]]:
    """生成设备/测点数据集。

    Args:
        per_type: 每种设备类型生成的台数，默认 5（共 60 台）。
            压测万行样例时调大，如 --per-type 2000 -> 2.4 万行设备。
    """
    device_rows: list[dict] = []
    point_rows: list[dict] = []
    for dtype, (prefix, point_types) in DEVICE_TYPES.items():
        for i in range(1, per_type + 1):
            code = f"{prefix}-{i:04d}"
            device_rows.append(
                {
                    "device_code": code,
                    "device_name": f"{i}号{dtype}",
                    "device_type": dtype,
                    "location": random.choice(AREAS),
                    "status": random.choice(STATUS),
                }
            )
            for ptype in point_types:
                point_rows.append(
                    {
                        "device_code": code,
                        "point_code": f"{prefix}{i:04d}_{POINT_CODE[ptype]}",
                        "point_name": f"{ptype}",
                        "point_type": ptype,
                        "unit": UNITS[ptype],
                    }
                )
    return device_rows, point_rows


def seed_database(device_rows: list[dict], point_rows: list[dict], *, reset: bool = False) -> None:
    with session_scope() as db:
        if reset:
            log.warning("resetting business tables (audit_log kept: append-only)")
            db.execute(delete(Record))
            db.execute(delete(Alarm))
            db.execute(delete(LineageEdge))
            db.execute(delete(LineageNode))
            db.execute(delete(MeasurePoint))
            db.execute(delete(Device))
            db.flush()

        with audit_as("seed-script"):
            # 1) 设备
            code_to_device: dict[str, Device] = {}
            for row in device_rows:
                device = Device(**row, version=1)
                db.add(device)
                code_to_device[row["device_code"]] = device
            db.flush()

            # 2) 测点
            code_to_point: dict[str, MeasurePoint] = {}
            for row in point_rows:
                device = code_to_device[row["device_code"]]
                point = MeasurePoint(
                    device_id=device.id,
                    point_code=row["point_code"],
                    point_name=row["point_name"],
                    point_type=row["point_type"],
                    unit=row["unit"],
                    version=1,
                )
                db.add(point)
                code_to_point[row["point_code"]] = point
            db.flush()

            # 3) 血缘：测点 -> 数据集 -> 报表/任务/接口
            for point in code_to_point.values():
                register_node(
                    db,
                    node_type="point",
                    node_key=point.point_code,
                    node_name=f"{point.point_name}({point.point_code})",
                    owner="设备管理部",
                )
            for key, name, owner in REPORTS:
                register_node(db, node_type="report", node_key=key, node_name=name, owner=owner)
            for key, name, owner in TASKS:
                register_node(db, node_type="task", node_key=key, node_name=name, owner=owner)
            for key, name, owner in APIS:
                register_node(db, node_type="api", node_key=key, node_name=name, owner=owner)
            register_node(
                db, node_type="dataset", node_key="DS_CLEAN_TS", node_name="清洗后时序宽表", owner="数据组"
            )

            # 每个测点随机挂 1~3 个下游
            downstream_pool = [k for k, _, _ in REPORTS] + [k for k, _, _ in TASKS] + [k for k, _, _ in APIS]
            for point in code_to_point.values():
                # 所有测点先进清洗数据集
                register_edge(
                    db,
                    upstream_key=point.point_code,
                    downstream_key="DS_CLEAN_TS",
                    upstream_type="point",
                    downstream_type="dataset",
                    relation="read",
                )
                for target in random.sample(downstream_pool, k=random.randint(1, 3)):
                    ntype = (
                        "report" if target.startswith("RPT") else "task" if target.startswith("TASK") else "api"
                    )
                    register_edge(
                        db,
                        upstream_key=point.point_code,
                        downstream_key=target,
                        upstream_type="point",
                        downstream_type=ntype,
                        field_name=point.point_code,
                        relation="read",
                    )
            # 数据集 -> 报表/任务
            for target in downstream_pool:
                ntype = (
                    "report" if target.startswith("RPT") else "task" if target.startswith("TASK") else "api"
                )
                register_edge(
                    db,
                    upstream_key="DS_CLEAN_TS",
                    downstream_key=target,
                    upstream_type="dataset",
                    downstream_type=ntype,
                    relation="aggregate",
                )

            # 4) 少量记录与告警，撑起数据量
            now = datetime.now(timezone.utc)
            sample_points = list(code_to_point.values())[:30]
            records = []
            for point in sample_points:
                for i in range(20):
                    records.append(
                        {
                            "device_id": point.device_id,
                            "point_id": point.id,
                            "value": round(random.uniform(20, 95), 2),
                            "ts": now - timedelta(minutes=i * 5),
                            "quality": "GOOD",
                        }
                    )
            db.bulk_insert_mappings(Record, records)

            alarms = []
            for point in random.sample(sample_points, k=8):
                alarms.append(
                    {
                        "device_id": point.device_id,
                        "point_id": point.id,
                        "alarm_level": random.choice(["INFO", "WARN", "CRITICAL"]),
                        "alarm_msg": f"{point.point_code} 超过阈值",
                        "triggered_at": now - timedelta(hours=random.randint(1, 72)),
                    }
                )
            db.bulk_insert_mappings(Alarm, alarms)

            AuditService.record(
                db,
                table_name="import",
                action="INSERT",
                operator="seed-script",
                business_key="seed",
                new={"devices": len(device_rows), "points": len(point_rows)},
                reason="初始化演示数据",
            )

    log_event(
        log,
        "seed.done",
        devices=len(device_rows),
        points=len(point_rows),
        lineage_edges="~" + str(len(point_rows) * 2 + len(REPORTS) + len(TASKS) + len(APIS)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 G05 演示数据")
    parser.add_argument("--reset", action="store_true", help="先清空业务表（审计表保留）")
    parser.add_argument("--excel-only", action="store_true", help="只生成 Excel 样例")
    parser.add_argument(
        "--per-type",
        type=int,
        default=5,
        help="每种设备类型的台数（默认 5，共 60 台）；压测万行样例可调大，如 2000",
    )
    args = parser.parse_args()

    device_rows, point_rows = generate_dataset(per_type=max(1, args.per_type))
    excel_path = PROJECT_ROOT / "data" / "sample" / "sample_devices.xlsx"
    build_excel(excel_path, device_rows, point_rows)

    if args.excel_only:
        print(f"[OK] 样例 Excel 已生成: {excel_path}")
        return

    seed_database(device_rows, point_rows, reset=args.reset)
    print(f"[OK] 演示数据已灌入：{len(device_rows)} 台设备 / {len(point_rows)} 个测点")
    print(f"[OK] 样例 Excel: {excel_path}")


if __name__ == "__main__":
    main()
