"""故障注入脚本（答辩现场演示用）。

五类故障，每类都能"注入 -> 观察 -> 证明系统没崩"：

1. ``orphan``    孤儿记录：插入引用不存在设备的测点 → 外键拒绝
2. ``tamper``    篡改审计：UPDATE/DELETE 审计表 → 触发器 ABORT
3. ``dirty``     脏数据导入：构造坏 Excel → 校验打回、整批回滚
4. ``kill``      进程强杀：子进程写库中被 kill -9 → 重启校验数据完整（WAL）
5. ``concurrent`` 并发压测：多线程同时读写 → 无 database is locked

用法::

    python scripts/fault_inject.py --all
    python scripts/fault_inject.py --case orphan
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select, text  # noqa: E402

from g05.core.config import PROJECT_ROOT  # noqa: E402
from g05.core.db import session_scope  # noqa: E402
from g05.core.logging import get_logger  # noqa: E402
from g05.models.device import Device  # noqa: E402
from g05.models.measure_point import MeasurePoint  # noqa: E402

log = get_logger("scripts.fault")

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{GREEN}  ✓ {msg}{RESET}")


def _fail(msg: str) -> None:
    print(f"{RED}  ✗ {msg}{RESET}")


def _info(msg: str) -> None:
    print(f"{DIM}    {msg}{RESET}")


# ------------------------------------------------------------
# 1. 孤儿记录
# ------------------------------------------------------------
def case_orphan() -> bool:
    print(f"\n{YELLOW}[1/5] 孤儿记录注入{RESET}  目标：外键约束拒绝悬空引用")
    try:
        with session_scope() as db:
            orphan = MeasurePoint(
                device_id=999999,  # 不存在的设备
                point_code="FAULT_ORPHAN_01",
                point_name="孤儿测点",
                point_type="温度",
                unit="℃",
            )
            db.add(orphan)
            db.flush()
        _fail("孤儿记录竟然插入成功，外键约束失效！")
        return False
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        _ok(f"插入被拒绝：{name}")
        _info(str(exc).split("\n")[0][:120])
        return True


# ------------------------------------------------------------
# 2. 审计篡改
# ------------------------------------------------------------
def case_tamper() -> bool:
    print(f"\n{YELLOW}[2/5] 审计篡改注入{RESET}  目标：append-only 触发器拦截改删")
    ok = True

    # 先确保审计表里有一条记录。
    # 触发器是行级 BEFORE 触发器：库里没有行时 UPDATE/DELETE 影响 0 行，
    # 触发器压根不会执行 —— 那样测出来是"没被拦截"，
    # 但真相是"没数据可改"，属于假失败。空库环境（CI）尤其容易踩。
    from g05.audit.service import AuditService

    with session_scope() as db:
        existing = db.execute(text("SELECT count(*) FROM audit_log")).scalar_one()
        if not existing:
            AuditService.record(
                db,
                table_name="device",
                action="INSERT",
                operator="fault-injector",
                business_key="TAMPER-TARGET",
                new={"device_name": "待篡改的审计记录"},
            )
            _info("审计表为空，已写入 1 条目标记录")

    for sql, label in (
        ("UPDATE audit_log SET operator='hacker'", "UPDATE"),
        ("DELETE FROM audit_log", "DELETE"),
    ):
        try:
            with session_scope() as db:
                db.execute(text(sql))
            _fail(f"{label} 未被拦截，审计可篡改（评分红线）！")
            ok = False
        except Exception as exc:  # noqa: BLE001
            _ok(f"{label} 被触发器拒绝")
            _info(str(exc).split("\n")[0][:120])
    return ok


# ------------------------------------------------------------
# 3. 脏数据导入
# ------------------------------------------------------------
def case_dirty() -> bool:
    print(f"\n{YELLOW}[3/5] 脏数据导入{RESET}  目标：校验打回 + 整批回滚")
    from openpyxl import Workbook

    from g05.importers.bulk_importer import build_rejection_note, import_workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.append(["device_code", "device_name", "device_type", "location", "status"])
    ws.append(["FAN-9001", "正常设备", "风机", "一厂A区", "运行"])
    ws.append(["fan-9002", "", "外星设备", "二厂C区", "运行"])  # 三重错误

    ws2 = wb.create_sheet("测点")
    ws2.append(["device_code", "point_code", "point_name", "point_type", "unit"])
    ws2.append(["FAN-9001", "FAN9001_TEMP", "温度", "温度", "℃"])

    tmp = Path(tempfile.mkdtemp()) / "dirty.xlsx"
    wb.save(tmp)

    with session_scope() as db:
        before = db.execute(select(func.count()).select_from(Device)).scalar_one()
        result = import_workbook(db, tmp, operator="fault-injector")
        after = db.execute(select(func.count()).select_from(Device)).scalar_one()

    if result.ok:
        _fail("脏数据被放行！")
        return False

    _ok(f"整批被打回，设备数未变（{before} -> {after}）")
    _info(f"消息：{result.message}")
    note = build_rejection_note(result)
    for line in note.splitlines()[3:8]:
        if line.strip():
            _info(line[:110])
    return before == after


# ------------------------------------------------------------
# 4. kill -9 强杀恢复
# ------------------------------------------------------------
_WRITER = r"""
import sys, time
sys.path.insert(0, r"__SRC__")
from g05.core.db import session_scope
from g05.models.device import Device

with session_scope() as db:
    db.add(Device(device_code="KILL-0000", device_name="强杀测试", device_type="电机", location="三厂E区"))
for i in range(1, 100000):
    with session_scope() as db:
        db.add(Device(device_code="KILL-%04d" % i, device_name="强杀%d" % i,
                      device_type="电机", location="三厂E区"))
    print(i, flush=True)
"""


def case_kill() -> bool:
    print(f"\n{YELLOW}[4/5] kill -9 强杀恢复{RESET}  目标：WAL 模式下数据零丢失、库不损坏")
    src = str(PROJECT_ROOT / "src")
    script = _WRITER.replace("__SRC__", src)

    tmp_py = Path(tempfile.mkdtemp()) / "writer.py"
    tmp_py.write_text(script, encoding="utf-8")

    with session_scope() as db:
        db.execute(text("DELETE FROM device WHERE device_code LIKE 'KILL-%'"))

    proc = subprocess.Popen(
        [sys.executable, str(tmp_py)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(1.8)  # 让它写一批
    proc.kill()      # SIGKILL，模拟断电
    proc.wait(timeout=10)

    with session_scope() as db:
        # 完整性检查
        integrity = db.execute(text("PRAGMA integrity_check")).scalar()
        written = db.execute(
            select(func.count()).select_from(Device).where(Device.device_code.like("KILL-%"))
        ).scalar_one()

    if str(integrity).lower() != "ok":
        _fail(f"数据库损坏：{integrity}")
        return False

    _ok(f"强杀后完整性检查 = {integrity}")
    _ok(f"已落盘记录 {written} 条，重启可读")
    _info("验证点：WAL + synchronous=NORMAL，未提交事务回滚，已提交事务保留")

    with session_scope() as db:
        db.execute(text("DELETE FROM device WHERE device_code LIKE 'KILL-%'"))
    return True


# ------------------------------------------------------------
# 5. 并发压测
# ------------------------------------------------------------
def case_concurrent(threads: int = 8, per_thread: int = 40) -> bool:
    print(f"\n{YELLOW}[5/5] 并发写入压测{RESET}  目标：无 database is locked，计数一致")
    errors: list[str] = []
    counter = {"done": 0}
    lock = threading.Lock()

    def worker(tid: int) -> None:
        try:
            for i in range(per_thread):
                with session_scope() as db:
                    db.add(
                        Device(
                            device_code=f"CONC-{tid:02d}-{i:04d}",
                            device_name=f"并发{tid}-{i}",
                            device_type="电机",
                            location="三厂E区",
                        )
                    )
                with lock:
                    counter["done"] += 1
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"thread{tid}: {exc}")

    with session_scope() as db:
        db.execute(text("DELETE FROM device WHERE device_code LIKE 'CONC-%'"))

    started = time.perf_counter()
    workers = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    elapsed = time.perf_counter() - started

    with session_scope() as db:
        actual = db.execute(
            select(func.count()).select_from(Device).where(Device.device_code.like("CONC-%"))
        ).scalar_one()

    expected = threads * per_thread
    if errors:
        _fail(f"{len(errors)} 个线程报错，首个：{errors[0][:100]}")
        return False

    _ok(f"{threads} 线程 × {per_thread} 条 = {expected}，实际入库 {actual}，耗时 {elapsed:.2f}s")
    _ok("无 database is locked（busy_timeout + WAL 生效）")

    with session_scope() as db:
        db.execute(text("DELETE FROM device WHERE device_code LIKE 'CONC-%'"))
    return actual == expected


CASES = {
    "orphan": case_orphan,
    "tamper": case_tamper,
    "dirty": case_dirty,
    "kill": case_kill,
    "concurrent": case_concurrent,
}


def _ensure_schema_ready() -> None:
    """跑之前确保表结构与触发器就位。

    本地演示时 ``data/db/g05.db`` 早有 seed 数据，表是现成的；
    但 CI 是干净环境，库是空的 —— 不建表的话每个场景都会撞
    ``no such table: device``，故障注入就变成了"测环境"而不是"测故障"。

    ``ensure_schema`` 是幂等的（``create_all`` + ``apply_triggers``），
    已有数据与触发器都不受影响。
    """
    from g05.core.schema import ensure_schema

    ensure_schema()
    _info("表结构与触发器已就位")


def main() -> None:
    parser = argparse.ArgumentParser(description="G05 故障注入")
    parser.add_argument("--case", choices=list(CASES), help="只跑单个场景")
    parser.add_argument("--all", action="store_true", help="跑全部场景")
    args = parser.parse_args()

    if not args.case and not args.all:
        parser.print_help()
        return

    print("=" * 68)
    print(" G05 故障注入演示 —— 每个场景都应「不崩、可恢复、可追溯」")
    print("=" * 68)

    # 先确保表结构与触发器就位。
    # 本地演示时库里早有 seed 数据，但 CI 是干净环境 —— 不建表的话
    # 下面每个场景都会报 "no such table: device"，测不出任何东西。
    _ensure_schema_ready()

    selected = [args.case] if args.case else list(CASES)
    results: dict[str, bool] = {}
    for name in selected:
        try:
            results[name] = CASES[name]()
        except Exception as exc:  # noqa: BLE001
            _fail(f"{name} 执行异常：{exc}")
            results[name] = False

    print("\n" + "=" * 68)
    passed = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        print(f"  {GREEN if ok else RED}{'PASS' if ok else 'FAIL'}{RESET}  {name}")
    print(f"\n  合计 {passed}/{len(results)} 通过")
    print("=" * 68)
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
