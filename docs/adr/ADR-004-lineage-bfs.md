# ADR-004 血缘影响面采用逐层 BFS

- **状态**：已接受
- **日期**：2026-10
- **决策者**：B2 血缘引擎负责人

## 背景

测点改名之前，需要在 1 秒内回答其上游或下游影响面，并给出每个结果的完整依赖路径。
当前存储是 SQLite 中的 `lineage_node`、`lineage_edge` 邻接表；典型查询深度不超过 10 层，
验收规模为三层、200 个结果节点。

## 决策

使用 Python 显式 frontier 的逐层 BFS：

1. 根节点在入队前加入 `visited`。
2. 每层用一条 `IN (frontier_ids)` 查询取出全部边，再用一条 `IN (candidate_ids)` 查询节点。
3. 新节点在入队时加入 `visited`，保证菱形依赖只返回一次。
4. 队列元素保存业务键路径和节点 id 路径；只有目标 id 已出现在当前路径时才报告环。
5. 到达 `max_depth` 停止扩展；尝试返回第 `max_nodes + 1` 个节点时标记
   `truncated=true`，不会静默丢弃。

时间复杂度为 `O(V + E)`，空间复杂度为 `O(V)`。在无截断的情况下，SQL 数量约为
`1 + 2 × 实际遍历层数`：一次根节点查询，每层一次边查询和一次节点查询，因此不会随节点数线性增长。

## 备选方案

| 方案 | 不采用的原因 |
|---|---|
| 递归函数 | 深图可能触发 Python 栈限制，且深度、路径与截断状态不易统一维护 |
| 每节点查邻居 | 产生 N+1，SQL 数量随节点数增长，不满足性能口径 |
| SQLite 递归 CTE | 路径与防环逻辑需要字符串编码，解释性和可测试性较差 |
| NetworkX | 当前功能只需 BFS，引入运行依赖增加离线部署与数据同步成本 |
| Neo4j | 需要独立服务、部署与运维；当前数据规模下属于过度设计 |
| 全图常驻内存 | 多进程缓存失效复杂，且低频改名查询不值得占用常驻内存 |

## 环检测语义

`visited` 命中本身不等于环。比如 `A→B、A→C、B→D、C→D` 是菱形汇聚，`D` 被第二条
路径再次遇到时只跳过，不报告循环。只有边指回当前路径中的祖先（如 `A→B→A`）才加入
`cycles`。数据库触发器 `trg_edge_no_self_loop` 另行拒绝自环，两者职责不同。

## 可观测性与保护

- 每次查询记录根节点、方向、返回数、层数、环数、截断状态和耗时。
- 耗时达到 `app.lineage.warn_ms`（未配置时为 1000 ms）记录 WARN。
- `app.lineage.max_depth` 与 `app.lineage.max_nodes` 限制异常图的查询成本。
- `upstream_id`、`downstream_id` 的索引由 B1 表结构负责，B2 不跨边界修改模型。

## 验证

`tests/test_lineage.py` 覆盖双向查询、完整路径、深度 0/1、缺失节点、二元环、菱形汇聚、
截断、自环触发器、图导出以及三层 200 节点性能。性能用例同时统计 SQL：

```bash
python -m pytest tests/test_lineage.py -q
python -m pytest tests/test_lineage.py -k 200_node -q
```

## Implementation clarification (2026-11)

- The frontier path still prevents a back-edge from being enqueued as a new node.
- Cycle reporting is produced by an iterative DFS over the collected subgraph so
  cycles hidden by a diamond-shaped BFS discovery path are still reported.
- Cycle output is rotated to the smallest node id before de-duplication, making
  equivalent cycles stable without changing the public response shape.
- Upstream traversal reverses the query columns only. Returned graph edges always
  keep the data-flow orientation `upstream -> downstream` for SVG rendering.

通过条件：200 个结果节点耗时 `<1s`，三层 BFS 的 `SELECT` 不超过 7 条。实际数字必须来自
完成 B1 模型合入后的真实测试输出，不在 ADR 中预填未经运行的结果。
