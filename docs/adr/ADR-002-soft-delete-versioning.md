# ADR-002 软删除 + 版本号，不做物理删除

- **状态**：已接受
- **日期**：2026-10（第 5 周）
- **决策者**：A 组长、B1 建模负责人
- **适用范围**：device、measure_point（资产类表）

## 背景

资产目录的"删除"与普通业务数据不同：设备/测点的历史变更本身就是资产，需要长期可查。
同时需求明确要求「改名不删除（软删/版本）」、审计要能回溯任一变更。

## 备选方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. 软删（`is_deleted`）+ 版本号（`version`）** | 行保留，历史可查；外键仍指向真实行；审计 old_value 有意义 | 所有查询都要带 `is_deleted=False`，易漏 |
| B. 物理删除 | 表干净、查询简单 | 历史丢失；审计记录变成悬空引用；外键 RESTRICT 会阻塞删除 |
| C. 双表（主表 + 历史表） | 主表精简，历史表可分区 | 写操作要同步两表，事务复杂；查询历史需 union |
| D. 时态表（valid_from/valid_to） | 支持"任意时点快照"查询 | 模型复杂度高；SQLite 无原生支持，需手写大量区间逻辑 |

## 决策

采用 **A**，并在关键字段上加辅助：

| 字段 | 作用 | 归属 |
|---|---|---|
| `is_deleted` | 软删标记 | SoftDeleteMixin |
| `deleted_at` | 删除时间，支撑保留期策略；软删时自动补齐 | SoftDeleteMixin |
| `version` | 每次变更 +1，用于展示"这是第几版" | SoftDeleteMixin |
| `change_note` | 变更原因，配合审计的 `reason` | SoftDeleteMixin |
| `renamed_from` | 改名场景保留旧名，血缘查询沿版本链回溯 | measure_point |
| `superseded_by` | 自引用指向接替它的新测点 | measure_point |

### 改名不产生新行

只更新 `point_name`，`version+1`，`renamed_from` 存旧名。**`point_code` 不变**。

因为血缘边挂在 `point_code` 上，编号不变 → 血缘边无需重连 →
改名的代价从"重建整张血缘图"降到"更新一行"。

这是选择"改 name 不改 code"的关键收益。

### 版本号由 ORM 自动维护

用 SQLAlchemy 原生的 `version_id_col`：

```python
class SoftDeleteMixin:
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))

    @declared_attr
    def __mapper_args__(cls):
        return {"version_id_col": cls.version}
```

好处有两个：

1. **业务层不可能"忘了 +1"**。分发包初版把版本号维护留给业务层，
   AI 协作指南里也把「version 没自增 → 业务层忘了 +1」列为常见翻车。
   用 `version_id_col` 后，翻车点从"依赖人的记性"变成"不可能发生"。
2. **白送乐观锁**。两个人同时改一行，后提交者会拿到 `StaleDataError` 而不是静默覆盖。

兼容性：业务层若仍习惯性写 `obj.version += 1`，其结果与自动自增一致（不会跳号），
存量代码迁移过来不会把版本号改乱 —— 但**新代码不要再手动 +1**。

### 外键 ondelete 分场景

| 关系 | 策略 | 理由 |
|---|---|---|
| measure_point → device | RESTRICT | 测点是资产，设备挂着测点时禁止物理删除，逼走软删流程 |
| record → measure_point | CASCADE | 采集值是宿主的行为数据，宿主不存在时无独立意义 |
| record → device | CASCADE | 同上 |
| alarm → device | CASCADE | 设备物理删除时其历史告警一并清理（以 `docs/02-架构设计.md` 的外键策略表为准） |
| alarm → measure_point | CASCADE | 同上 |
| lineage_edge → lineage_node | CASCADE | 边是派生数据，随宿主清理 |
| measure_point.superseded_by → measure_point | SET NULL | 接替者被删则断链，不牵连本行 |

**关键：业务层用软删，`ondelete` 只在物理删除时生效。别把软删和级联混在一起谈。**

### 查询默认过滤已删

服务层（C4）的列表查询统一走 `alive_filter(model, include_deleted=False)`：

```python
stmt = select(MeasurePoint).where(alive_filter(MeasurePoint))
```

`include_deleted=True` 时返回恒真条件（查看回收站场景）。
提供**单一入口**是为了避免"每个接口各写一遍、漏一处就漏出已删数据"。

### 唯一约束不因软删释放

`device_code` / `point_code` 的唯一约束对已软删行**依然生效** ——
编号一旦使用不可复用，符合工业资产编码规范。

代价：软删后重建同名记录会撞唯一键。这是刻意选择：
资产编号复用会让历史记录指向错误的新资产，比"重建时要换个号"危险得多。

## 后果

**正面**
- 历史零丢失，审计 `old_value` 永远指向真实存在过的数据
- 外键约束始终有效（不会因为删除导致悬空引用）
- 可回答"这个测点以前叫什么"这类追溯问题
- 版本号不可能忘记递增

**负面**
- 查询必须显式过滤 `is_deleted=False`，漏写会返回已删数据
- 表会随时间膨胀（资产量级小，可接受）
- 唯一约束需注意：软删后重建同名记录会撞唯一键
- `version_id_col` 带来乐观锁语义：并发修改会抛 `StaleDataError`，
  业务层需捕获并给出"数据已被他人修改，请刷新"的用户提示

**缓解措施**
- 查询过滤集中在 `alive_filter` 一个入口
- 唯一约束加在业务键上，软删不释放业务键
- 设备/测点物理删除用 `RESTRICT` 拦住（有子表就不许删），业务层一律走软删

## 验证

```bash
pytest tests/test_models.py -q                                    # 36 passed
pytest tests/test_models.py -k "soft_delete or version" -q
pytest tests/test_models.py -k "physical_delete_device_with_points_blocked" -q
```

覆盖的行为：

| 验收项 | 测试 | 结果 |
|---|---|---|
| 软删后行仍在 | `test_soft_delete_keeps_row` | ✅ |
| 软删自动补 deleted_at | `test_soft_delete_stamps_deleted_at` | ✅ |
| 软删不减少物理行数 | `test_soft_delete_does_not_change_row_count` | ✅ |
| 默认列表查不到已删 | `test_alive_filter_excludes_deleted_by_default` | ✅ |
| include_deleted 开关能查到 | `test_alive_filter_with_include_deleted` | ✅ |
| 连改 3 次 version 依次 2/3/4 | `test_version_increments_automatically` | ✅ |
| 改名只 version+1 并写 renamed_from | `test_rename_keeps_row_and_records_history` | ✅ |
| 有测点的设备物理删除被拒 | `test_physical_delete_device_with_points_blocked` | ✅ |
| 告警随设备物理删除级联清理 | `test_alarms_cascade_deleted_with_device` | ✅ |
| 血缘边自环被拒 | `test_lineage_edge_self_loop_rejected` | ✅ |
