# ADR-001 选用 SQLAlchemy 2.0 声明式 + SQLite

- **状态**：已接受
- **日期**：2026-10（第 5 周）
- **决策者**：A 组长、B1 建模负责人
- **适用范围**：设备/测点/记录/告警/审计/血缘共 7 张表

## 背景

需要为设备资产目录建模，并满足硬指标：

- 孤儿记录插入必须被库拒绝（外键约束真实生效）
- 万行导入 < 30s
- 血缘查询 < 1s
- 课程环境不允许部署独立数据库服务

## 备选方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. SQLAlchemy 2.0 + SQLite** | 声明式约束表达力强；`Mapped` 类型提示友好；单文件免运维；WAL 支持崩溃恢复 | SQLite 单写者，高并发写会串行 |
| B. 原生 sqlite3 + 手写 SQL | 零抽象开销，性能最高 | 外键/关系/级联全靠手写；易漏 `PRAGMA foreign_keys=ON`；改表结构成本高 |
| C. Django ORM | 生态成熟、admin 开箱即用 | 需引入整个 Django；本项目无模板渲染需求，过重 |
| D. Peewee | 轻量、API 简洁 | 2.0 风格的异步/类型支持弱；社区资料少于 SQLAlchemy |
| E. PostgreSQL | 真并发、约束能力强、类型系统完善 | 需部署服务；课程环境无 DB 服务器；运维成本远超项目规模 |

### 为什么不用 PostgreSQL

PostgreSQL 在**并发写入**与**约束表达力**上确实优于 SQLite，但本项目的现实约束让它不成立：

1. **环境不允许**：课程环境不提供独立数据库服务，PostgreSQL 需自行部署、初始化、配账号密码，
   每个人都得在本地装服务 —— 这是纯粹的交付摩擦，且不产生业务价值。
2. **负载不需要**：资产目录的写入是"运维人员维护台账"，QPS 是个位数；
   血缘查询是只读 QPS 也就几十。SQLite 的单写者瓶颈在真实写入路径上根本触不到。
3. **SQLite 够用且有对应补偿**：WAL 模式让读写不互相阻塞，`busy_timeout=5000` 让并发写等待而非报错，
   8 线程压测 0 冲突。真正的瓶颈是"一人一个库文件"的部署形态，PG 也解决不了。
4. **迁移成本可控**：ORM 层隔离方言。若日后 PG 变得必要，改动集中在
   `config/database.yaml` 的连接串与 `core/db.py` 的 PRAGMA 钩子（按方言跳过），
   模型层与业务层无需改动。

**但要说清楚代价**：如果未来出现多用户高并发写入，或需要真正的行级并发控制，
应当直接迁 PG 而不是继续给 SQLite 打补丁。本 ADR 的有效期以"单机、少量写入"为前提。

### 为什么不用原生 sqlite3

外键、关系、级联全靠手写，每处都容易漏。尤其 `PRAGMA foreign_keys=ON` 是**连接级**开关，
漏设一次孤儿记录就能插进去，且测试环境往往因连接复用而"看起来是好的"。

## 决策

采用 **A**。理由：

1. **外键约束的可靠性**：`ForeignKey(..., ondelete="RESTRICT")` 声明即生效，
   配合连接级 `PRAGMA foreign_keys=ON`，孤儿记录必被拒。方案 B 需要每处手写校验，容易漏。
2. **类型安全**：`Mapped[str]` / `mapped_column(String(32))` 让字段类型与长度在代码里可见，减少运行时错误。
3. **可迁移性**：ORM 层隔离方言，换 PostgreSQL 只需改连接串与 PRAGMA 钩子。
4. **性能可接受**：批量导入走 `bulk_insert_mappings` 绕过 ORM 逐行开销。

## 关键实现细节

### 1. SQLAlchemy 2.0 写法，不混用 1.x

```python
# ✅ 正确
class Device(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "device"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)

# ❌ 错误（1.x 风格，本项目不用）
id = Column(Integer, primary_key=True)
```

### 2. `PRAGMA foreign_keys=ON` 必须每连接设置

SQLite 的外键默认**关闭**，且该 PRAGMA 是**连接级会话变量**，不是库级设置：
连接池会新建/复用连接，只在 `create_engine` 时设一次，后续新建的连接外键又是关的。
必须在 `connect` 事件里对**每条**新连接执行：

```python
@event.listens_for(engine, "connect")
def _apply_pragmas(dbapi_conn, _record):
    for key, value in settings.get("database.database.pragma", {}).items():
        dbapi_conn.execute(f"PRAGMA {key}={value}")
```

具体 PRAGMA 取值来自 `config/database.yaml`（配置外置铁律，代码里不写死数字）：

```yaml
database:
  pragma:
    journal_mode: "WAL"
    synchronous: "NORMAL"
    busy_timeout: 5000
    foreign_keys: "ON"      # 外键约束必须显式开启（SQLite 默认关闭！）
    cache_size: -64000
    temp_store: "MEMORY"
```

健康检查里 `foreign_keys` 必须为真，否则直接报错退出 —— 建库脚本宁可失败也不能建出一个
"看起来能用、实际拦不住孤儿记录"的库。

### 3. `ondelete` 按场景分（详见 ADR-002）

业务层用软删，`ondelete` 只在物理删除时生效。两者不要混谈。

### 4. `lazy` 策略按访问模式选

- 列表页要带出所属设备 → `lazy="selectin"`（避免 N+1）
- 单条详情才用到、或是海量表 → `lazy="select"`
- 绝不无脑用 `lazy="joined"`，大表会拖垮查询
- 物理删除交给数据库 → `passive_deletes=True`，不在 ORM 里逐条加载子表

### 5. 时间统一 UTC

所有时间列用 `UTCDateTime`（TypeDecorator）：写入前折算到 UTC，读出后补回 `timezone.utc`。
SQLite 无原生时区类型，直接用 `DateTime(timezone=True)` 会读出 naive 对象，
调用方当本地时间用就会差 8 小时。

### 6. 索引不是越多越好

`is_deleted` 单独建索引几乎没用（基数只有 2），要建**复合索引**：`(device_id, is_deleted)`。
建完必须用 `EXPLAIN QUERY PLAN` 验证真的走了索引，否则删掉。

## 后果

**正面**
- 约束、索引、关系在模型层一处声明，DDL 自动生成
- 测试可用 `Base.metadata.create_all` 快速建库
- 审计埋点可挂在 Session 事件上，业务代码零侵入

**负面**
- SQLite 单写者：并发写入串行。已用 `busy_timeout=5000` + WAL 缓解，8 线程压测 0 冲突；
  但真高并发需迁库
- ORM 抽象有学习成本：新人需理解 `session.dirty` / `before_flush` 才能改审计逻辑
- `server_default` 必须与 Python 侧 `default` 同步维护：绕开 ORM 的裸 SQL 写入
  （如 C1 批量导入）不经过 Python 层默认值，缺 `server_default` 会因 NOT NULL 先失败，
  掩盖真正该报的错误（如外键违例）

**缓解措施**
- 慢查询钩子（>200ms 打 WARN）监控 ORM 生成的低效 SQL
- 关键查询用 `select()` 显式构造，不用字符串拼接
- `config/database.yaml` 集中所有 PRAGMA 与连接池参数，调优不改代码

## 验证

```bash
pytest tests/test_models.py -q          # 38 passed
python scripts/init_db.py --drop         # 7 张表 + 3 个触发器
```

实测输出（2026-11-01）：

```
[OK] 数据库自检通过
  foreign_keys     = True
  journal_mode     = wal
  db_path          = .../data/db/g05.db

[表清单] 共 7 张表
  - alarm / audit_log / device / lineage_edge / lineage_node / measure_point / record

[触发器清单] 共 3 个
  - trg_audit_no_delete
  - trg_audit_no_update
  - trg_edge_no_self_loop
```

> 触发器由 A 维护在 `core/schema.py`（`TRIGGERS` + `apply_triggers`），
> B1 的 `scripts/init_db.py` 只负责调用，不自己写一份 —— 两处定义必然漂移。

孤儿记录插入实测：

```bash
sqlite3 data/db/g05.db "INSERT INTO measure_point(device_id,point_code,point_name,point_type,unit) VALUES (999999,'X','x','温度','℃');"
# FOREIGN KEY constraint failed
```

索引命中实测（`EXPLAIN QUERY PLAN`）：

```
SEARCH measure_point USING INDEX ix_point_device_active (device_id=? AND is_deleted=?)
SEARCH measure_point USING INDEX ix_point_type_active  (point_type=? AND is_deleted=?)
SEARCH device         USING INDEX ix_device_type_status (device_type=? AND status=?)
SEARCH record         USING INDEX ix_record_point_ts     (point_id=? AND ts>?)
SEARCH device         USING INDEX ix_device_device_code  (device_code=?)
```
