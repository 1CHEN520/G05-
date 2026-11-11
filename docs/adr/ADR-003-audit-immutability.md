# ADR-003 审计不可篡改：应用层 + 数据库触发器双保险

- **状态**：已接受
- **日期**：2026-10（第 5 周）
- **决策者**：A 组长、C3 审计负责人

## 背景

评分标准红线写明：「密码明文/审计可改 = 该维度 0 分」。审计表的可信度必须能被**现场证明**，而不是靠"我们代码里没写 update 语句"这种口头承诺。

## 备选方案

| 方案 | 优点 | 缺点 | 能否现场证明 |
|---|---|---|---|
| A. 只靠应用层约定（不提供 update/delete 方法） | 实现简单 | 任何直连数据库的操作都能绕过；无法自证 | ✗ |
| B. 应用层 + 数据库触发器 | 绕过应用层也改不了 | 需 SQLite 触发器支持 | ✓ |
| C. 哈希链（每条记录含前一条的 hash） | 可检测篡改 | 只能"事后发现"，不能"阻止"；查询变慢 | 部分 |
| D. 独立审计库 + 只写账号 | 权限隔离最彻底 | 需要独立 DB 服务，课程环境不允许 | ✓（但不可行） |

## 决策

采用 **B**，并保留 C 的演进空间。

**两道防线**

| 防线 | 位置 | 机制 |
|---|---|---|
| 应用层 | `AuditService` | 只暴露 `record()` 写入，**不提供** update/delete 方法；查询只读 |
| 数据库层 | `trg_audit_no_update` / `trg_audit_no_delete` | `BEFORE UPDATE/DELETE ON audit_log → RAISE(ABORT, '...append-only...')` |

触发器 DDL 集中在 `src/g05/core/schema.py`，由 `init_db.py`、应用启动、测试夹具三处共用，**避免"生产有触发器、测试没有"的行为漂移**（这个坑实际踩过，见踩坑记录 #5）。

**现场验证接口**：`POST /api/audit/verify-immutable` 直接对审计表发 `UPDATE` 和 `DELETE`，返回：

```json
{
  "immutable": true,
  "checks": [
    {"operation": "UPDATE", "blocked": true, "detail": "audit_log is append-only: UPDATE forbidden"},
    {"operation": "DELETE", "blocked": true, "detail": "audit_log is append-only: DELETE forbidden"}
  ]
}
```

## 后果

**正面**
- 即使拿到数据库文件的攻击者，也无法通过 SQL 修改审计
- 答辩时不需要解释"我们的代码没写删除"，直接跑一条 SQL 让评委看报错
- 触发器是 DDL 级约束，任何 ORM/驱动/命令行工具都绕不过

**负面**
- 审计表无法清理，长期会膨胀
- 触发器带来极小的写入开销（每次 UPDATE 多一次 BEFORE 检查）
- 测试若误删审计记录会直接失败，调试时需要 `DROP TRIGGER`

**缓解措施**
- 审计表按年归档到只读文件（`config/database.yaml` 的 `archive` 段预留），不删在线数据
- 审计表只建必要索引，避免写放大
- 测试夹具统一用 `apply_triggers()`，需要绕过时显式 `DROP TRIGGER` 并注释原因

## 演进方向

若未来合规要求"可检测篡改"而非仅"阻止篡改"，在 `audit_log` 增加：

```
prev_hash  TEXT   -- 前一条记录的 sha256
self_hash  TEXT   -- sha256(prev_hash + 本条内容)
```

查询时校验链完整性，可定位到被篡改的具体记录。当前方案已能阻止，故暂不引入（避免过度设计）。

## 实现要点（复核后补充，均为实测结论）

### 1. 验证接口必须用独立 session + SAVEPOINT

用**独立 session**，不污染请求上下文；每次篡改尝试用 `SAVEPOINT` 圈起来，
失败只回滚到保存点，会话不会进入 aborted 状态，后续查询照常可用。

```python
savepoint = probe_session.begin_nested()
try:
    probe_session.execute(text(sql))
    savepoint.rollback()          # 没被拦截：连"篡改成功"也一起回滚
except Exception as exc:
    savepoint.rollback()          # ← 少了这句，会话进入 aborted 状态，后续查询全报错
```

`detail` 截断到 160 字符——SQLite 的错误串可能很长，前端展示会撑破布局。

### 2. 空表时"没拦住"是假阴性（真踩过的坑）

`UPDATE audit_log SET operator='x' WHERE id=(SELECT MIN(id) FROM audit_log)`
在**空表**时匹配 0 行，SQLite 根本不触发 `BEFORE UPDATE` 触发器，语句成功返回。
如果据此判定"没拦住"，就是**误报合规问题**。

实测对照：

| 场景 | 结果 |
|---|---|
| 库里至少有一条记录 | ✅ 报 `append-only: UPDATE forbidden` |
| 空表 + 无 WHERE | ⚠️ 语句成功（匹配 0 行），**不代表触发器失效** |

因此 `verify-immutable` 在空表时会先插入一条探针记录再试篡改，
探针随整个事务一起回滚，不留在库里。

> ⚠️ 更隐蔽的一种：**跳过篡改尝试**也算"全部拦截"。
> 所以 `verify-immutable` 除了返回 `immutable=true`，还必须逐项列出
> 实际执行过的操作（UPDATE / DELETE），由用例断言"确实试过"，
> 避免空断言被当成验证通过。

### 3. 旧值必须在**加载时**拍快照

`loaded_as_persistent` 触发时属性还是库里的原值；`before_flush` 触发时
业务代码已经把属性改完了，此时取到的是**新值**，old == new，这条审计是废的。

实测（`test_auto_audit_update_old_value_is_before_change`）：

```
加载后:      对象上的值 = 旧名    _g05_old_snapshot = 旧名
flush 后:    对象上的值 = 新名    _g05_old_snapshot = 旧名（仍是旧值）  审计表 1 条 UPDATE
```

### 4. 审计写入放在 `after_flush`，不是 `before_flush`

`before_flush` 时自增主键与列默认值**还没解析**（实测 `id=None, version=None,
is_deleted=None`），写进审计就是假数据。`after_flush` 时才拿到真实值：

```
INSERT 审计 new_value = {"id": 1, "device_code": "P9-1", "version": 1, ...}
```

同时 `after_flush` 还要**刷新快照**——本次落库后的值就是下次修改的"旧值"。
不刷新的话，同一 session 里连续改两次，第二次的 `old_value` 仍是第一次之前的老快照。

实测（`test_auto_audit_second_update_old_value_is_previous_value`）：

```
第1条: old='v0' -> new='v1'
第2条: old='v1' -> new='v2'     ← 修复前这里是 'v0'
```

### 5. 事件挂在 Session 类上，不是 sessionmaker 实例上

sessionmaker 实例的事件**只对该实例产出的 Session 生效**。
测试里常用 `Session(bind=engine)` 另开 Session，那种情况会**漏埋点**。
类级监听对所有 Session 生效，配合"每次 flush 只登记一次"的护栏，既不漏也不重复。

实测：`expire_on_commit` × `autoflush` 四种组合下，审计语义完全一致。

### 6. 时间统一 UTC

SQLite 存的是 UTC 墙钟时间，读出来 `tzinfo=None`。
若直接拿 `+08:00` 的入参比较，边界会错 8 小时——时间范围查询会悄悄查错一段区间。

`AuditLog.changed_at` 复用 `models/base.py` 的 `UTCDateTime`（全项目统一的时间语义），
查询过滤前把入参统一转成 UTC 朴素时间。

## 变更覆盖度自证

每处实现改动都有对应的**失败用例**兜着——把实现改回去，用例必须变红。
用变异测试逐条验证（每条都确实导致 FAIL）：

| 变异 | 被哪条用例抓住 |
|---|---|
| INSERT 审计回到 `before_flush`（主键未解析） | `test_auto_audit_insert_snapshot_has_resolved_id_and_defaults` |
| 去掉 `after_flush` 的快照刷新 | `test_auto_audit_second_update_old_value_is_previous_value` |
| 去掉软删识别 | `test_auto_audit_soft_delete_marked_as_soft_delete` |
| 埋点挂 sessionmaker 实例 | `test_auto_audit_covers_session_created_directly` 等 16 条 |
| 去掉安装幂等守卫 | `test_install_auto_audit_does_not_register_duplicate_listeners` |
| `total` 用当前页条数 | `test_query_total_is_total_count_not_page_size` |
| 去掉空表探针守卫 | `test_verify_immutable_actually_attempts_tamper` 等 2 条 |
| 旧值改为 flush 时现取 | 4 条用例同时变红 |
| `dirty` 分支不检查 skip 标记 | 去重相关用例 |

## 验证

```bash
python scripts/fault_inject.py --case tamper
curl -X POST http://127.0.0.1:8000/api/audit/verify-immutable
pytest tests/test_audit.py -q
```
