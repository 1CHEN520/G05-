# ADR-001 选用 SQLAlchemy 2.0 声明式 + SQLite

- **状态**：已接受
- **日期**：2026-10（第 5 周）
- **决策者**：A-01 组长、B-01 建模负责人

## 背景

需要为设备/测点/记录/告警四表建模，并满足硬指标：孤儿记录插入必须被库拒绝、万行导入 <30s、血缘查询 <1s。课程环境不允许部署独立数据库服务。

## 备选方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. SQLAlchemy 2.0 + SQLite** | 声明式约束表达力强；`Mapped` 类型提示友好；单文件免运维；WAL 支持崩溃恢复 | SQLite 单写者，高并发写会串行 |
| B. 原生 sqlite3 + 手写 SQL | 零抽象开销，性能最高 | 外键/关系/级联全靠手写；易漏 `PRAGMA foreign_keys=ON`；改表结构成本高 |
| C. Django ORM | 生态成熟、admin 开箱即用 | 需引入整个 Django；本项目无模板渲染需求，过重 |
| D. Peewee | 轻量、API 简洁 | 2.0 风格的异步/类型支持弱；社区资料少于 SQLAlchemy |

## 决策

采用 **A**。理由：

1. **外键约束的可靠性**：`ForeignKey(..., ondelete="RESTRICT")` 声明即生效，配合连接级 `PRAGMA foreign_keys=ON`，孤儿记录必被拒。方案 B 需要每处手写校验，容易漏。
2. **类型安全**：`Mapped[str]` / `mapped_column(String(32))` 让字段类型与长度在代码里可见，减少运行时错误。
3. **可迁移性**：ORM 层隔离方言。换 PostgreSQL 只需改 `config/database.yaml` 的连接串（`PRAGMA` 钩子按方言跳过）。
4. **性能可接受**：批量导入走 `bulk_insert_mappings` 绕过 ORM 逐行开销，实测 5k+5k 行约 3.2s，远低于 30s 上限。

## 后果

**正面**
- 约束、索引、关系在模型层一处声明，DDL 自动生成
- 测试可用 `Base.metadata.create_all` 快速建库，46 个单测跑完 3s
- 审计埋点可挂在 Session 事件上，业务代码零侵入

**负面**
- SQLite 单写者：并发写入串行。已用 `busy_timeout=5000` + WAL 缓解，8 线程压测 0 冲突；但真高并发需迁库
- ORM 抽象有学习成本：新人需理解 `session.dirty` / `before_flush` 才能改审计逻辑

**缓解措施**
- 慢查询钩子（>200ms 打 WARN）监控 ORM 生成的低效 SQL
- 关键查询用 `select()` 显式构造，不用字符串拼接
- `config/database.yaml` 集中所有 PRAGMA 与连接池参数，调优不改代码

## 验证

```bash
pytest tests/test_models.py -q          # 7 passed，含孤儿记录、WAL、外键开关
pytest tests/test_importer.py -q        # 含万行导入性能守护
```
