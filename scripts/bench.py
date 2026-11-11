"""性能基线脚本（G05-31）。

测四类指标，每类跑三次取中位数：

1. ``import``    批量导入（Excel → 入库）
2. ``lineage``   血缘影响面查询
3. ``pagination``列表分页查询
4. ``audit``     审计查询

用法::

    python scripts/bench.py                    # 默认规模
    python scripts/bench.py --devices 500      # 更大数据量
    python scripts/bench.py --repeat 5         # 跑 5 次取中位数
    python scripts/bench.py --json out.json    # 同时输出 JSON

设计约束（来自《任务指导书-C5》）：
- **三次取中位数**：单次测量会被 GC / 磁盘缓存干扰，中位数更稳
- **记录环境信息**：CPU / 内存 / 磁盘 / OS，不写清楚数字就没有可比性
- **写清复现命令**：报告里每个数字都要能按命令复现
- **不减小数据量换好看数字**：数据量如实打印

脚本只读不写业务库：所有数据写进临时目录的独立 SQLite，跑完即删。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select, text  # noqa: E402

from g05.core.config import settings  # noqa: E402
from g05.core.db import get_engine, reset_engine, session_scope  # noqa: E402
from g05.core.schema import apply_triggers  # noqa: E402
from g05.models import Base  # noqa: E402
from g05.models.audit_log import AuditLog  # noqa: E402
from g05.models.device import Device  # noqa: E402
from g05.models.measure_point import MeasurePoint  # noqa: E402

# 指标目标（毫秒）。达不到会在报告里明确写出，不粉饰。
TARGETS_MS = {
    "import": 5000.0,
    "lineage": 200.0,
    "pagination": 100.0,
    "audit": 200.0,
}

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


# ------------------------------------------------------------
# 环境信息
# ------------------------------------------------------------
def _read_memory_windows() -> tuple[float | None, float | None]:
    """不依赖 psutil 读内存总量与可用量（Windows）。

    用 ``GlobalMemoryStatusEx`` —— 比 ``/proc`` 路线更准，也不必装第三方包。
    非 Windows 或调用失败时返回 (None, None)。
    """
    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None, None
        return (
            round(stat.ullTotalPhys / 1024**3, 2),
            round(stat.ullAvailPhys / 1024**3, 2),
        )
    except Exception:  # noqa: BLE001
        return None, None


def collect_env_info(db_path: Path) -> dict[str, Any]:
    """收集环境信息 —— 没有它，性能数字没有可比性。"""
    info: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()}",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
        "processor": platform.processor() or "未知",
    }

    try:
        import psutil  # type: ignore[import-not-found]

        vm = psutil.virtual_memory()
        info["memory_total_gb"] = round(vm.total / 1024**3, 2)
        info["memory_available_gb"] = round(vm.available / 1024**3, 2)
    except ImportError:
        # psutil 非必需依赖，退回标准库读 Windows 全局内存状态
        info["memory_total_gb"], info["memory_available_gb"] = _read_memory_windows()
    except Exception:  # noqa: BLE001
        info["memory_total_gb"] = None
        info["memory_available_gb"] = None

    try:
        usage = shutil.disk_usage(str(db_path.parent))
        info["disk_free_gb"] = round(usage.free / 1024**3, 2)
        info["disk_total_gb"] = round(usage.total / 1024**3, 2)
    except Exception:  # noqa: BLE001
        info["disk_free_gb"] = None
        info["disk_total_gb"] = None

    try:
        with session_scope() as db:
            info["sqlite_version"] = db.execute(text("SELECT sqlite_version()")).scalar()
            info["journal_mode"] = db.execute(text("PRAGMA journal_mode")).scalar()
    except Exception:  # noqa: BLE001
        info["sqlite_version"] = None
        info["journal_mode"] = None

    return info


# ------------------------------------------------------------
# 数据准备
# ------------------------------------------------------------
def seed_data(device_count: int, points_per_device: int) -> dict[str, Any]:
    """灌入基准数据。返回统计信息。

    编号同样要符合 ``config/rules.yaml``：``device_code`` 用
    ``^[A-Z]{2,4}-\\d{3,6}$``、``point_code`` 用 ``^[A-Z0-9_]{2,48}$``。
    """
    with session_scope() as db:
        devices = [
            Device(
                device_code=f"BEN-{i:06d}",
                device_name=f"压测设备{i:06d}",
                device_type=["风机", "水泵", "电机", "减速机"][i % 4],
                location=f"压测区{i % 20:02d}",
                status="运行",
            )
            for i in range(device_count)
        ]
        db.add_all(devices)
        db.flush()

        points = []
        for d in devices:
            for j in range(points_per_device):
                points.append(
                    MeasurePoint(
                        device_id=d.id,
                        point_code=f"BEN{d.id:06d}P{j:03d}",
                        point_name=f"压测点{j:03d}",
                        point_type=["温度", "振动", "压力", "流量"][j % 4],
                        unit="℃",
                    )
                )
        db.add_all(points)
        db.flush()

        # 审计数据也要有量，否则审计查询测的是空表
        audits = [
            AuditLog.build(
                table_name="device",
                action="INSERT",
                operator=f"bench{i % 5}",
                business_key=f"BEN-{i:06d}",
                new={"device_name": f"压测设备{i:06d}"},
            )
            for i in range(min(device_count, 2000))
        ]
        db.add_all(audits)
        db.flush()

        total_points = db.execute(select(func.count()).select_from(MeasurePoint)).scalar_one()
        total_audits = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    return {
        "devices": device_count,
        "points": total_points,
        "audits": total_audits,
        "points_per_device": points_per_device,
    }


# ------------------------------------------------------------
# 四类指标
# ------------------------------------------------------------
def bench_import(sample_devices: int, sample_points: int, db_path: Path) -> float:
    """测 Excel 批量导入耗时（毫秒）。

    **每次调用都用独立的库文件** —— 否则第 2 次跑时第 1 次的数据还在库里，
    ``unique`` 校验会让整批被拒，测到的变成"重复导入"的耗时而不是"首次导入"。

    注意：编号格式必须符合 ``config/rules.yaml``：
    - ``device_code``：``^[A-Z]{2,4}-\\d{3,6}$``（如 ``FAN-0012``）
    - ``point_code``：``^[A-Z0-9_]{2,48}$``（只能大写字母/数字/下划线）

    否则整批会被校验打回，测到的就成了"失败路径"的耗时，失去意义。
    """
    from openpyxl import Workbook

    from g05.importers.bulk_importer import import_workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    for i in range(sample_devices):
        ws.append([f"BEN-{i:06d}", f"导入设备{i}", "风机", "压测区", "运行"])

    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    for i in range(sample_devices):
        for j in range(sample_points):
            ws2.append([f"BEN-{i:06d}", f"BEN{i:06d}P{j:02d}", f"导入点{j}", "温度", "℃"])

    # 独立库目录：与主基准库分开，保证每次都是"空库首次导入"
    run_dir = Path(tempfile.mkdtemp(prefix="g05-bench-import-", dir=str(db_path.parent)))
    run_db = run_dir / "import.db"
    tmp_xlsx = run_dir / "bench_import.xlsx"
    wb.save(tmp_xlsx)

    # 记住主库路径，测完指回去 —— 否则后续血缘/分页/审计会跑到空库上
    main_db_path = settings._raw["database"]["database"]["path"]  # noqa: SLF001
    try:
        settings._raw["database"]["database"]["path"] = str(run_db)  # noqa: SLF001
        reset_engine()
        engine = get_engine()
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            apply_triggers(conn)

        started = time.perf_counter()
        with session_scope() as db:
            result = import_workbook(db, tmp_xlsx, operator="bench")
        elapsed_ms = (time.perf_counter() - started) * 1000

        if not result.ok:
            raise RuntimeError(f"基准数据被校验打回，测的不是成功路径：{result.message}")
        inserted = getattr(result, "inserted", {}) or {}
        total_inserted = sum(v for v in inserted.values() if isinstance(v, int))
        if total_inserted == 0:
            raise RuntimeError(
                f"导入成功但一条都没入库，基准数据规模无效：inserted={inserted}"
            )

        return elapsed_ms
    finally:
        # 无论如何都把数据库指回主库
        settings._raw["database"]["database"]["path"] = main_db_path  # noqa: SLF001
        reset_engine()
        tmp_xlsx.unlink(missing_ok=True)
        shutil.rmtree(run_dir, ignore_errors=True)


def bench_lineage(node_key: str) -> float:
    """测血缘影响面查询耗时（毫秒）。"""
    from g05.lineage.impact import impact_of

    with session_scope() as db:
        started = time.perf_counter()
        impact_of(db, node_type="point", node_key=node_key, direction="downstream")
        return (time.perf_counter() - started) * 1000


def bench_pagination(page: int = 1, size: int = 20) -> float:
    """测列表分页查询耗时（毫秒）。"""
    from g05.services.device_service import list_devices
    from g05.services.point_service import list_points

    with session_scope() as db:
        started = time.perf_counter()
        list_points(db, page=page, size=size)
        list_devices(db, page=page, size=size)
        return (time.perf_counter() - started) * 1000


def bench_audit(limit: int = 200) -> float:
    """测审计查询耗时（毫秒）。"""
    from g05.audit.service import AuditService

    with session_scope() as db:
        started = time.perf_counter()
        AuditService.query(db, limit=limit)
        return (time.perf_counter() - started) * 1000


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def measure(label: str, fn, repeat: int) -> dict[str, Any]:
    """跑 repeat 次取中位数。"""
    samples: list[float] = []
    for _ in range(repeat):
        samples.append(fn())
    median = statistics.median(samples)
    return {
        "label": label,
        "median_ms": round(median, 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "samples_ms": [round(s, 2) for s in samples],
        "repeat": repeat,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="G05 性能基线")
    parser.add_argument("--devices", type=int, default=200, help="设备数量")
    parser.add_argument("--points-per-device", type=int, default=5, help="每台设备的测点数")
    parser.add_argument("--repeat", type=int, default=3, help="每类指标重复次数（取中位数）")
    parser.add_argument("--json", type=str, default=None, help="同时输出 JSON 到指定路径")
    args = parser.parse_args()

    tmp_dir = Path(tempfile.mkdtemp(prefix="g05-bench-"))
    db_file = tmp_dir / "bench.db"

    # 把数据库指向临时目录，绝不碰业务库
    settings._raw["database"]["database"]["path"] = str(db_file)  # noqa: SLF001
    reset_engine()

    print("=" * 68)
    print(" G05 性能基线报告")
    print("=" * 68)
    print(f"{DIM}临时库：{db_file}{RESET}")
    print()

    try:
        engine = get_engine()
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            apply_triggers(conn)

        env = collect_env_info(db_file)
        print(f"{BOLD}【环境信息】{RESET}")
        for key, value in env.items():
            print(f"  {key:<22} {value}")
        print()

        print(f"{DIM}灌入基准数据 ...{RESET}")
        data = seed_data(args.devices, args.points_per_device)
        print(f"{DIM}数据规模：{data}{RESET}")
        print()

        # 血缘需要一个真实节点
        with session_scope() as db:
            first_point = db.execute(select(MeasurePoint).limit(1)).scalar_one()
            node_key = first_point.point_code if first_point else "BENCH-NONE"

        results: list[dict[str, Any]] = []

        print(f"{BOLD}【四类指标】（每类 {args.repeat} 次，取中位数）{RESET}")
        print("-" * 68)

        # 1. 导入
        imp = measure(
            "批量导入",
            lambda: bench_import(20, 3, db_file),
            args.repeat,
        )
        imp["metric"] = "import"
        imp["data_scale"] = "20 设备 × 3 测点"
        results.append(imp)

        # 2. 血缘
        lin = measure("血缘影响面查询", lambda: bench_lineage(node_key), args.repeat)
        lin["metric"] = "lineage"
        lin["data_scale"] = f"节点 {node_key}"
        results.append(lin)

        # 3. 分页
        pag = measure("列表分页查询", lambda: bench_pagination(), args.repeat)
        pag["metric"] = "pagination"
        pag["data_scale"] = f"{data['devices']} 设备 / {data['points']} 测点，page=1 size=20"
        results.append(pag)

        # 4. 审计
        aud = measure("审计查询", lambda: bench_audit(), args.repeat)
        aud["metric"] = "audit"
        aud["data_scale"] = f"{data['audits']} 条审计，limit=200"
        results.append(aud)

        for r in results:
            target = TARGETS_MS.get(r["metric"], 0)
            ok = r["median_ms"] <= target
            color = GREEN if ok else RED
            mark = "达标" if ok else "未达标"
            print(f"{r['label']:<18} 中位数 {r['median_ms']:>9.2f} ms  "
                  f"(min {r['min_ms']:.2f} / max {r['max_ms']:.2f})  "
                  f"目标 {target:.0f} ms  {color}{mark}{RESET}")
            print(f"{DIM}    数据规模：{r['data_scale']}{RESET}")
            print(f"{DIM}    各次耗时：{r['samples_ms']} ms{RESET}")

        print()
        print(f"{BOLD}【未达标项说明】{RESET}")
        unmet = [r for r in results if r["median_ms"] > TARGETS_MS.get(r["metric"], 0)]
        if not unmet:
            print("  无 —— 四类指标全部达标")
        for r in unmet:
            print(f"  {r['label']}：中位数 {r['median_ms']:.2f} ms "
                  f"> 目标 {TARGETS_MS[r['metric']]:.0f} ms")
            print(f"{DIM}    复现：python scripts/bench.py --devices {args.devices} "
                  f"--points-per-device {args.points_per_device} --repeat {args.repeat}{RESET}")

        print()
        print(f"{BOLD}【复现命令】{RESET}")
        print(f"  python scripts/bench.py --devices {args.devices} "
              f"--points-per-device {args.points_per_device} --repeat {args.repeat}")

        report = {
            "env": env,
            "data_scale": data,
            "results": results,
            "targets_ms": TARGETS_MS,
            "reproduce": (
                f"python scripts/bench.py --devices {args.devices} "
                f"--points-per-device {args.points_per_device} --repeat {args.repeat}"
            ),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        if args.json:
            out = Path(args.json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{DIM}JSON 报告已写入：{out}{RESET}")

        print("=" * 68)
    finally:
        reset_engine()
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
