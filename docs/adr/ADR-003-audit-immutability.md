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

## 验证

```bash
python scripts/fault_inject.py --case tamper
curl -X POST http://127.0.0.1:8000/api/audit/verify-immutable
pytest tests/test_audit.py -q
```
