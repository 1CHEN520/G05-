# G05 运行手册（Runbook）

> 面向运维/接手的同学。按本文档操作，无需读源码即可完成部署、配置、监控与故障处理。

## 一、环境准备

### 1.1 依赖

| 项 | 要求 | 检查命令 |
|---|---|---|
| Python | ≥ 3.10 | `python --version` |
| 磁盘 | ≥ 500MB（含日志与归档） | — |
| 内存 | ≥ 512MB | — |
| 网络 | **不需要**（离线可运行） | — |

### 1.2 安装

```bash
# 课程环境
conda activate pp03

# 或自建虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux/macOS

pip install -r requirements.txt
```

### 1.3 目录初始化

首次运行会自动创建 `data/db/`、`logs/`。如需手工确认：

```bash
mkdir -p data/db data/archive logs
```

---

## 二、启动与停止

### 2.1 标准启动（三步）

```bash
python scripts/init_db.py       # 1. 建表 + 装触发器（幂等，可重复执行）
python scripts/seed_data.py     # 2. 灌演示数据（可选，生产环境跳过）
python -m g05.main              # 3. 启动服务
```

Windows 用户可双击 `run.bat`（自动完成上述三步）。

### 2.2 生产启动（多进程）

```bash
# 单 worker（SQLite 单写者，不建议多 worker）
uvicorn g05.main:app --host 0.0.0.0 --port 8000 --workers 1
```

> ⚠️ **不要用 `--workers > 1`**：SQLite 单写者模型下，多 worker 会加剧锁竞争。需要横向扩展请先迁 PostgreSQL。

### 2.3 停止

| 场景 | 操作 |
|---|---|
| 前台运行 | `Ctrl+C`（触发 graceful shutdown，等事务提交完） |
| 后台运行 | `kill -TERM <pid>`（**不要用 `-9`**，会中断在途事务） |
| 强杀（仅故障演练） | `kill -9 <pid>`，重启后 WAL 自动恢复 |

### 2.4 重启后自检

```bash
curl http://127.0.0.1:8000/healthz
```

期望：

```json
{
  "ok": true,
  "database": {"ok": true, "foreign_keys": true, "journal_mode": "wal"},
  ...
}
```

`ok: false` 的常见原因见 §五。

---

## 三、配置说明

所有配置在 `config/*.yaml`，**改配置不改代码**。改完重启生效（除日志级别，可热改）。

### 3.1 `config/app.yaml`

| 键 | 默认 | 说明 |
|---|---|---|
| `server.host` | `127.0.0.1` | 监听地址，对外服务改 `0.0.0.0` |
| `server.port` | `8000` | 端口 |
| `import.max_rows` | `200000` | 单次导入行数上限 |
| `import.chunk_size` | `1000` | bulk 分片大小，内存紧张时调小 |
| `import.allow_update` | `false` | 是否允许更新已存在记录 |
| `lineage.max_depth` | `10` | 血缘遍历深度上限 |
| `lineage.max_nodes` | `5000` | 影响面返回节点上限 |

### 3.2 `config/database.yaml`

| 键 | 默认 | 说明 |
|---|---|---|
| `database.path` | `data/db/g05.db` | 数据库文件路径 |
| `pragma.journal_mode` | `WAL` | **不要改**，改了失去崩溃恢复 |
| `pragma.busy_timeout` | `5000` | 锁等待毫秒，并发高时调大 |
| `pool.size` | `10` | 连接池大小 |
| `slow_query_ms` | `200` | 慢查询告警阈值 |

### 3.3 `config/rules.yaml`（业务最常改）

**改校验规则**：

```yaml
sheets:
  device:
    columns:
      device_code:
        regex: "^[A-Z]{2,4}-\\d{3,6}$"     # 改这里立即生效
```

**改放行分数线**：

```yaml
scoring:
  pass_threshold: 85     # 低于此分数打回
```

**新增设备类型**：

```yaml
device_type:
  enum: ["风机", "水泵", ..., "新设备类型"]   # 在枚举里加一项
```

### 3.4 `config/logging.yaml`

| 键 | 默认 | 说明 |
|---|---|---|
| `handlers.file_json.maxBytes` | `10485760` | 单文件 10MB 轮转 |
| `handlers.file_json.backupCount` | `5` | 保留 5 个历史文件 |
| `loggers.g05.level` | `DEBUG` | 生产建议 `INFO` |

### 3.5 环境变量覆盖

支持 `G05_` 前缀 + `__` 分隔层级覆盖 YAML：

```bash
G05_SERVER__PORT=9000 G05_IMPORT__MAX_ROWS=50000 python -m g05.main
```

> 适合容器化/临时调试，**不建议**作为常规配置方式（配置散落难排查）。

---

## 四、监控

### 4.1 健康检查

```bash
curl http://127.0.0.1:8000/healthz
```

检查三项：DB 连通、外键开启、WAL 生效。

### 4.2 运行指标

```bash
curl http://127.0.0.1:8000/metrics
# {"devices": 60, "points": 180, "audit_entries": 42}
```

### 4.3 日志

| 文件 | 内容 | 用途 |
|---|---|---|
| `logs/app.jsonl` | 全量结构化日志（JSON Lines） | 按字段检索 |
| `logs/error.jsonl` | ERROR 及以上 | 告警排查 |
| `logs/server.out` | uvicorn 标准输出（如重定向） | 启动问题 |

**常用检索**

```bash
# 查某次导入
grep '"import' logs/app.jsonl | tail -5

# 查慢查询
grep 'slow query' logs/app.jsonl

# 查某操作人的全部动作
grep '"operator":"zhangsan"' logs/app.jsonl
```

### 4.4 慢查询告警

超过 `slow_query_ms`（默认 200ms）的 SQL 打 WARN：

```json
{"ts":"...","level":"WARNING","logger":"g05.core.db","msg":"slow query",
 "data":{"elapsed_ms":412.3,"sql":"SELECT ..."}}
```

处理：用 `EXPLAIN QUERY PLAN` 检查是否走索引，必要时补索引。

### 4.5 关键监控项

| 指标 | 阈值 | 处理 |
|---|---|---|
| `/healthz` 返回 `ok:false` | 立即告警 | 见 §五 |
| 日志 ERROR 条数 | >10/min | 查 `error.jsonl` |
| 数据库文件大小 | >2GB | 检查记录表增长，考虑归档 |
| 导入耗时 | >30s | 检查 `chunk_size` 与磁盘 IO |
| 血缘查询耗时 | >1s | 检查 `lineage_edge` 索引 |

---

## 五、故障处理

### 5.1 `ok: false` — 外键未开启

**现象**：`/healthz` 返回 `foreign_keys: false`

**怎么判断**：
1. `curl -s http://127.0.0.1:8000/healthz` 确认 `foreign_keys: false` 且其余项正常
2. `sqlite3 data/db/g05.db "PRAGMA foreign_keys;"` —— 返回 `0` 确认是连接层 PRAGMA 没生效，不是误报

**原因**：SQLite 默认关闭外键，`PRAGMA` 未生效

**处理**：
1. 确认 `config/database.yaml` 的 `pragma.foreign_keys: "ON"` 存在
2. 确认 `core/db.py` 的 `_apply_pragmas` 挂在 `connect` 事件上
3. 重启服务

### 5.2 `ok: false` — WAL 未生效

**现象**：`journal_mode` 不是 `wal`

**怎么判断**：
1. `sqlite3 data/db/g05.db "PRAGMA journal_mode;"` —— 返回非 `wal` 确认问题
2. `netstat -ano | findstr :8000` 之外再查有没有别的进程连着这个 .db（WAL 会被以非 WAL 模式打开的旧连接压回去）
3. 确认数据库文件不在网络盘上：`net use` 看映射盘符

**原因**：数据库文件被其他进程以 `DELETE` 模式打开；或文件系统不支持（网络盘）

**处理**：
```bash
sqlite3 data/db/g05.db "PRAGMA journal_mode=WAL;"
```
确认没有其他进程占用；不要放在网络共享盘上。

### 5.3 `database is locked`

**现象**：写入报 `sqlite3.OperationalError: database is locked`

**怎么判断**：
1. `grep -c 'database is locked' logs/app.jsonl` 确认日志里确有此错误
2. `netstat -ano | findstr :8000` + `tasklist | findstr python` 找是否有残留服务进程（上次没关的服务是头号嫌疑）
3. `sqlite3 data/db/g05.db "PRAGMA wal_checkpoint(TRUNCATE);"` 能正常执行 → 说明锁已释放，问题在并发时序；执行不了 → 还有进程持有写锁

**原因**：并发写超时

**处理**：
1. 临时：调大 `config/database.yaml` 的 `pragma.busy_timeout`（如 10000）
2. 根本：检查是否有长事务未提交；减少并发写；考虑迁 PostgreSQL
3. 排查：`PRAGMA wal_checkpoint(TRUNCATE)` 手动检查点

### 5.4 导入一直被打回

**现象**：`POST /api/import/excel` 返回 `ok: false`

**处理**：
1. 看 `reports[].issues`，每条含 `row`（Excel 行号）/ `column` / `rule` / `message`
2. 常见原因：
   - `required`：必填列空
   - `regex`：编号格式不符（如小写、缺横杠）
   - `enum`：类型不在白名单
   - `foreign_key`：测点的 `device_code` 在设备表里不存在
   - `unique`：编号重复
3. 先用 `POST /api/import/validate` 预检，不写库

### 5.5 血缘查询返回空

**现象**：`/api/lineage/impact` 返回 `total: 0`

**怎么判断**：
1. `curl -s http://127.0.0.1:8000/api/lineage/graph | python -m json.tool | head -20` —— 看 graph 里有没有这个节点（节点都没有 vs 节点在边不在，处理完全不同）
2. `sqlite3 data/db/g05.db "SELECT * FROM lineage_node WHERE node_key='FAN0001_VIB';"` —— 确认节点已注册
3. `sqlite3 data/db/g05.db "SELECT * FROM lineage_edge WHERE upstream_key='FAN0001_VIB';"` —— 确认边是否存在

**原因**：该测点没有注册血缘边

**处理**：
```bash
# 注册节点
curl -X POST http://127.0.0.1:8000/api/lineage/nodes \
  -H "Content-Type: application/json" \
  -d '{"node_type":"point","node_key":"FAN0001_VIB","node_name":"振动"}'

# 注册边
curl -X POST http://127.0.0.1:8000/api/lineage/edges \
  -H "Content-Type: application/json" \
  -d '{"upstream_key":"FAN0001_VIB","downstream_key":"RPT_OEE_DAILY",
       "upstream_type":"point","downstream_type":"report"}'
```

### 5.6 审计写入失败

**现象**：报 `audit_log is append-only`

**原因**：代码里尝试 UPDATE/DELETE 审计表（触发器拦截）

**处理**：这不是 bug，是设计。审计只能 `INSERT`。若确实需要清理，只能归档到外部文件，不能删在线数据。

### 5.7 数据库文件损坏

**现象**：`PRAGMA integrity_check` 不为 `ok`

**怎么判断**：
1. `sqlite3 data/db/g05.db "PRAGMA integrity_check;"` —— 输出不是 `ok` 即损坏；输出 `ok` 则不是库损坏，回 §5.1–5.3 排查
2. `ls -lh data/db/` —— 看 `-wal`/`-shm` 文件是否异常大（WAL 没做检查点）
3. `grep -c ERROR logs/error.jsonl | tail -1` —— 排查是不是磁盘满导致的写坏

**处理**：
```bash
# 1. 备份
cp data/db/g05.db data/db/g05.db.bak

# 2. 尝试导出重建
sqlite3 data/db/g05.db ".recover" | sqlite3 data/db/g05_new.db

# 3. 校验新库
sqlite3 data/db/g05_new.db "PRAGMA integrity_check;"
```
若无法恢复，从最近的备份还原。**不要**在损坏库上继续写入。

### 5.8 端口被占用

```bash
# Windows
netstat -ano | findstr :8000
taskkill /PID <pid> /F

# Linux
lsof -i :8000
kill -9 <pid>
```

或改 `config/app.yaml` 的 `server.port`。

---

## 六、日常运维任务

| 周期 | 任务 | 命令 |
|---|---|---|
| 每日 | 检查 `/healthz` | `curl .../healthz` |
| 每日 | 检查 ERROR 日志 | `grep ERROR logs/error.jsonl \| tail` |
| 每周 | 检查库大小 | `ls -lh data/db/` |
| 每周 | 检查慢查询 | `grep 'slow query' logs/app.jsonl \| wc -l` |
| 每月 | 审计数据导出备份 | `curl .../api/audit/export > audit_$(date).csv` |
| 每月 | 记录表归档（>90天） | 见 `config/database.yaml` 的 `archive` 段（预留） |
| 每季 | 全库备份 | `cp data/db/g05.db backups/g05_$(date).db` |

### 备份与恢复

```bash
# 备份（WAL 模式下必须先做检查点）
sqlite3 data/db/g05.db "PRAGMA wal_checkpoint(TRUNCATE);"
cp data/db/g05.db backups/g05_$(date +%Y%m%d).db

# 恢复
systemctl stop g05          # 或 kill -TERM
cp backups/g05_20261023.db data/db/g05.db
rm -f data/db/g05.db-wal data/db/g05.db-shm
systemctl start g05
```

---

## 七、故障演练

答辩现场或例行演练：

```bash
python scripts/fault_inject.py --all
```

| 场景 | 命令 | 期望 |
|---|---|---|
| 孤儿记录 | `--case orphan` | 外键拒绝 |
| 篡改审计 | `--case tamper` | 触发器拦截 |
| 脏数据导入 | `--case dirty` | 整批打回、零写入 |
| 强杀恢复 | `--case kill` | 库完整、数据可读 |
| 并发压测 | `--case concurrent` | 无锁冲突、计数一致 |

---

## 八、升级与回滚

### 升级

```bash
git pull
pip install -r requirements.txt    # 依赖有变时
python scripts/init_db.py          # 建新表/触发器（幂等）
# 重启服务
```

> 若涉及表结构变更，需手工迁移。本项目未引入 Alembic（规模不需要），变更前务必备份。

### 回滚

```bash
git checkout <上一个稳定 tag>
pip install -r requirements.txt
# 还原备份的数据库（若结构有变）
# 重启服务
```

---

## 九、交接清单

新接手同学按顺序确认：

- [ ] `pip install -r requirements.txt` 成功
- [ ] `python scripts/init_db.py` 输出 `[OK] 数据库自检通过`
- [ ] `python -m g05.main` 能打开 `http://127.0.0.1:8000`
- [ ] `pytest -q` 全绿
- [ ] 读过 `docs/01-需求规格.md` 与 `docs/02-架构设计.md`
- [ ] 跑过一遍 `python scripts/fault_inject.py --all`
- [ ] 知道改校验规则只改 `config/rules.yaml`
- [ ] 知道审计表不能改删，这是设计不是 bug
