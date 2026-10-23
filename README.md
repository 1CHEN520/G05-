# G05 设备台账与测点资产目录

> 工业级 L2 · 重庆两江智造集团数字化转型办公室 · 《Python 程序设计（项目实战）》期末项目
>
> **一句话**：把散在 Excel 里的 12 类设备台账收进一个带血缘追踪、变更可审计的资产目录系统。

[![tests](https://img.shields.io/badge/pytest-46%20passed-brightgreen)]() [![python](https://img.shields.io/badge/python-3.10%2B-blue)]() [![db](https://img.shields.io/badge/SQLite-WAL-orange)]()

---

## 一、开题四问（必答）

### 1. 断电重启会怎样？

**不丢数。**

- SQLite 启用 `journal_mode=WAL` + `synchronous=NORMAL`：已提交事务在崩溃后由 WAL 回放，未提交事务整体回滚
- 所有写操作包在 `session_scope()` 事务里，不存在"写一半"的中间态
- 审计表 append-only，重启后历史记录完整

**实证**：`python scripts/fault_inject.py --case kill` —— 子进程连续写库时被 `SIGKILL` 强杀，重启后 `PRAGMA integrity_check = ok`，已落盘 800+ 条记录全部可读。

### 2. 并发上来扛得住吗？

**扛得住，且有数字。**

- 连接池 `pool_size=10, max_overflow=20`，`busy_timeout=5000` 避免 `database is locked`
- WAL 允许读写并发（读不阻塞写）
- 批量导入走 `bulk_insert_mappings` + 分片提交，绕过 ORM 逐行开销

**实测**：8 线程 × 40 条并发写入，320 条全部入库，0 报错，耗时 0.19s；万行 Excel 导入 <30s（单测 `test_import_10k_rows_under_30s` 守护）。

### 3. 出问题能查到人/根因吗？

**能，且改不了。**

- 审计表 `audit_log` 记录 **谁 / 何时 / 改了哪张表的哪条 / 改前改后快照 / 变更原因**
- 数据库触发器 `trg_audit_no_update` / `trg_audit_no_delete` 直接 `RAISE(ABORT)` —— **绕过应用层也改不了审计**
- 血缘查询能定位"改这个测点，哪些报表/任务会受影响"，给出完整依赖路径

**实证**：`POST /api/audit/verify-immutable` 现场对审计表发 `UPDATE`/`DELETE`，均被数据库拒绝。

### 4. 换台机器跑得起来吗？

**跑得起来，一条命令。**

```bash
pip install -r requirements.txt
python scripts/init_db.py && python scripts/seed_data.py
python -m g05.main
```

- 配置全部外置在 `config/*.yaml`，改配置不改代码
- 依赖版本锁定在 `requirements.txt`
- 无需独立数据库服务（SQLite 单文件）
- 无外网依赖（前端零 CDN，图表用原生 SVG）

### 5. 怎么对应软件工程流程及国家标准和 TOGAF？

见 [`docs/03-流程与标准映射.md`](docs/03-流程与标准映射.md)：七阶段 SLA 对照 GB/T 8566 软件生存周期过程，架构产物按 TOGAF ADM 八阶段归档。

---

## 二、快速开始

### 环境

```bash
# 课程环境
conda activate pp03

# 或自建
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

### 三步跑通

```bash
python scripts/init_db.py          # 1. 建表 + 装防篡改触发器（幂等）
python scripts/seed_data.py        # 2. 灌入 60 台设备 / 180 个测点 / 血缘图
python -m g05.main                 # 3. 启动，浏览器开 http://127.0.0.1:8000
```

Windows 用户可直接双击 `run.bat`。

### 入口

| 地址 | 说明 |
|---|---|
| `http://127.0.0.1:8000/` | Web 控制台（总览 / 台账 / 测点 / 血缘 / 审计 / 导入） |
| `http://127.0.0.1:8000/docs` | Swagger API 文档 |
| `http://127.0.0.1:8000/healthz` | 健康检查（含真实 DB 探测） |
| `http://127.0.0.1:8000/metrics` | 运行指标 |

---

## 三、五条需求 → 实现位置

| # | 需求 | 实现 | 验收方式 |
|---|---|---|---|
| 1 | ER 设计：设备/测点/记录/告警四表 | `src/g05/models/{device,measure_point,record,alarm}.py` | 四表 + 审计表 + 血缘双表，共 7 表 |
| 2 | ORM 声明式模型 + 外键约束 | SQLAlchemy 2.0 `Mapped` / `mapped_column`，`PRAGMA foreign_keys=ON` | 插孤儿记录被拒（`test_orphan_point_rejected`） |
| 3 | 测点改名影响面查询（血缘） | `src/g05/lineage/impact.py` BFS + `POST /api/points/{id}/rename` | 查询 <1s，返回依赖路径 |
| 4 | 批量导入 Excel 与校验报告 | `src/g05/importers/` + `config/rules.yaml` | 万行 <30s，坏行定位到 Excel 行号 |
| 5 | 变更历史审计表 | `src/g05/models/audit_log.py` + `src/g05/audit/service.py` | 任一变更可回溯，改删被触发器拒绝 |

### 硬指标实测

| 指标 | 目标 | 实测 |
|---|---|---|
| 万行导入 | <30s | 5k 设备 + 5k 测点 ≈ 3.2s（单测守护） |
| 孤儿记录插入 | 被库拒绝 | `FOREIGN KEY constraint failed` |
| 血缘查询 | <1s | 单点下游 11 个节点 ≈ 6ms |
| 审计可回溯 | 任一变更 | 含 old/new 快照 + 操作人 + 原因 |
| 单测 | ≥15 个全绿 | **46 passed** |

---

## 四、目录结构

```
G05/
├── config/                     # 配置外置（改配置不改代码）
│   ├── app.yaml                # 应用参数：端口/导入上限/血缘深度
│   ├── database.yaml           # SQLite PRAGMA、连接池、归档策略
│   ├── rules.yaml              # ★ 导入校验规则 + 质量评分权重
│   └── logging.yaml            # 结构化 JSON 日志
├── src/g05/
│   ├── core/                   # 配置中心 / 日志 / 引擎 / 触发器 DDL
│   ├── models/                 # 7 张表：device, measure_point, record, alarm,
│   │                           #        audit_log, lineage_node, lineage_edge
│   ├── importers/
│   │   ├── validator.py        # ★ YAML 规则解释器 + 质量评分
│   │   └── bulk_importer.py    # 整批原子导入 + 打回单
│   ├── lineage/
│   │   └── impact.py           # ★ BFS 影响面 + 环检测 + 图导出
│   ├── audit/
│   │   └── service.py          # 审计写入口 + SQLAlchemy 自动埋点
│   ├── services/
│   │   └── point_service.py    # 改名（带影响面）/ 软删 / 版本链
│   ├── api/routes/             # devices, points, lineage, audit, imports
│   ├── schemas/dto.py          # Pydantic 出入参
│   └── main.py                 # FastAPI 入口
├── tests/                      # 46 个单测
├── scripts/
│   ├── init_db.py              # 建表 + 触发器 + 自检
│   ├── seed_data.py            # 演示数据 + 样例 Excel
│   └── fault_inject.py         # ★ 五类故障注入（答辩演示）
├── web/index.html              # 零依赖单页控制台
├── docs/                       # 需求/架构/ADR/运行手册/演示脚本
├── data/
│   ├── db/                     # SQLite 文件（.gitignore）
│   └── sample/                 # 样例导入工作簿
└── logs/                       # 结构化日志
```

---

## 五、核心设计要点

### 5.1 改名不删除（版本链）

测点改名不产生新行，而是：

```
point_name = 新名
version   += 1
renamed_from = 旧名      ← 保留历史
change_note  = 变更原因   ← 审计可读
```

审计表同步写入 `RENAME` 记录，含 old/new 快照。改名接口**先算影响面再改名**，返回受影响的下游报表/任务清单。

### 5.2 审计不可篡改（双保险）

| 防线 | 位置 | 机制 |
|---|---|---|
| 应用层 | `AuditService` | 只暴露 `record()`，无 update/delete 方法 |
| 数据库层 | `trg_audit_no_*` | `BEFORE UPDATE/DELETE ... RAISE(ABORT)` |

现场演示：`POST /api/audit/verify-immutable` 直接发 SQL 篡改，返回 `{"immutable": true}`。

### 5.3 校验规则配置化

`config/rules.yaml` 里改一行，行为立即变化，**不碰代码**：

```yaml
sheets:
  device:
    columns:
      device_code:
        required: true
        unique: true
        regex: "^[A-Z]{2,4}-\\d{3,6}$"    # 改这里就改了校验
scoring:
  weights: { completeness: 40, compliance: 35, uniqueness: 25 }
  pass_threshold: 85                       # 改这里就改了放行线
```

**放行 = 分数 ≥ 阈值 且 无结构性错误**。必填缺失 / 主键重复 / 外键悬空属结构性错误，一票否决，不能靠高分抵消。

### 5.4 导入整批原子

```
解析 → 校验 → 有错？整批不写，出打回单（带 Excel 行号）
              ↓ 无错
          bulk_insert 分片提交 → 任一分片失败 → 全部 rollback
```

---

## 六、故障注入（现场答辩）

```bash
python scripts/fault_inject.py --all
```

| # | 场景 | 期望 | 实测 |
|---|---|---|---|
| 1 | 孤儿记录 | 外键拒绝 | ✓ `FOREIGN KEY constraint failed` |
| 2 | 篡改审计 | 触发器拦截 | ✓ UPDATE/DELETE 均被 ABORT |
| 3 | 脏数据导入 | 整批打回、零写入 | ✓ 设备数 60 → 60 |
| 4 | kill -9 强杀 | 库不损坏、数据可读 | ✓ `integrity_check = ok` |
| 5 | 8 线程并发 | 无锁冲突、计数一致 | ✓ 320/320，0.19s |

---

## 七、测试

```bash
pytest -q                    # 46 passed
pytest --cov=src/g05         # 覆盖率
```

覆盖：模型约束（7）、校验引擎（10）、血缘（10）、审计（8）、导入（9）、服务（2）。

---

## 八、贡献表

| 编号 | 角色 | 模块 | commits |
|---|---|---|---|
| A-01 | 组长/架构 | 架构设计、接口冻结、集成验收 | — |
| B-01 | ORM/建模 | `models/`、索引、外键策略 | — |
| B-02 | 血缘引擎 | `lineage/`、影响面算法 | — |
| C-01 | 导入模块 | `importers/bulk_importer.py` | — |
| C-02 | 校验报告 | `importers/validator.py`、质量周报 | — |
| C-03 | 审计中间件 | `audit/`、触发器 | — |
| C-04 | CRUD/查询 | `api/routes/`、`services/` | — |
| C-05 | 测试压测 | `tests/`、`scripts/fault_inject.py` | — |
| C-06 | 文档运维 | `README`、`docs/`、`web/` | — |

> `git shortlog -sn` 现场可查。

---

## 九、文档索引

| 文档 | 内容 |
|---|---|
| [`docs/00-项目框架与分工方案.md`](docs/00-项目框架与分工方案.md) | 可行性分析、需求、架构、WBS、9 人分工、RACI |
| [`docs/01-需求规格.md`](docs/01-需求规格.md) | 用户故事 + 非功能需求（带数字） |
| [`docs/02-架构设计.md`](docs/02-架构设计.md) | 组件图、ER 图、接口契约、部署图 |
| [`docs/03-流程与标准映射.md`](docs/03-流程与标准映射.md) | 七阶段 SLA ↔ GB/T 8566 ↔ TOGAF ADM |
| [`docs/runbook.md`](docs/runbook.md) | 运行手册：部署、配置、监控、故障处理 |
| [`docs/demo_script.md`](docs/demo_script.md) | 答辩演示脚本（10 分钟逐句） |
| [`docs/踩坑记录.md`](docs/踩坑记录.md) | 实施过程中的 8 个坑与解法 |
| [`docs/adr/`](docs/adr/) | 架构决策记录（4 篇） |
