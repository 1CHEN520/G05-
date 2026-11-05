# ADR-004 血缘影响面用逐层 BFS，不用递归 SQL

- **状态**：已接受
- **日期**：2026-10（第 6 周）
- **决策者**：B2 血缘引擎负责人

## 背景

需求：「测点改名影响面查询（血缘）」，硬指标 **<1 秒**。数据形态：180 个测点、约 370 条依赖边，未来可能到 50 万测点。

影响面 = 从某测点出发，沿依赖边向下游（或上游）找到所有直接/间接消费方，并给出**依赖路径**（用于解释"为什么受影响"）。

## 备选方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. 逐层 BFS（每层一条 `IN` 查询）** | 深度可控；天然检测环；每层 1 次查询无 N+1；路径易记录 | 深度大时查询次数线性增长（本场景 ≤10 层） |
| B. 递归 CTE（`WITH RECURSIVE`） | 一条 SQL 搞定 | SQLite 递归 CTE 无法直接返回路径数组；环检测需额外逻辑；调试困难 |
| C. 全量加载内存图 | 查询最快（纯内存 BFS） | 节点量大时内存爆；多进程部署时数据不一致 |
| D. 邻接表预计算（物化闭包） | 查询 O(1) | 每次加边都要重算闭包；写放大严重 |

## 决策

采用 **A**。

**核心实现**（`src/g05/lineage/impact.py`）：

```
queue ← [(root, depth=0, path=[root_key])]
visited ← {root.id}

while queue:
    current, depth, path ← queue.popleft()
    if depth >= max_depth: continue
    next_ids ← SELECT downstream_id WHERE upstream_id = current.id
    rows     ← SELECT * FROM lineage_node WHERE id IN next_ids     ← 一层一条查询
    for row in rows:
        if row.id in visited: cycles.append(path + [row.key])       ← 回边即环
        else: visited.add; results.append(node with path); queue.append
```

**为什么不是递归 CTE**：需要返回完整路径（`A → DS → RPT`）用于影响面说明。递归 CTE 在 SQLite 里拼接路径数组要 `group_concat`，语义脆弱且难调试。BFS 在 Python 侧维护 `path` 列表，一目了然。

**为什么不是全量内存图**：影响面查询是低频操作（改名时才触发），没必要为它常驻几百 MB 内存。

## 后果

**正面**
- 实测单点下游 11 个节点约 **6ms**，远低于 1s 指标
- 环检测天然内建：`visited` 命中即记一条 `cycles`，不额外遍历
- 路径完整可解释，前端可直接展示 `FAN0001_VIB → DS_CLEAN_TS → RPT_OEE_DAILY`
- 深度上限来自配置 `app.lineage.max_depth`，防恶意构造的长链打爆

**负面**
- 深度每 +1 多一次查询。深度 10、每层 100 个节点 → 最多 10 次查询，仍远快于递归 CTE 的复杂度
- 多进程部署时 `visited` 是进程内的，不影响正确性（每次查询独立构建）

**保护措施**
- `max_nodes`（默认 5000）截断，返回 `truncated: true`
- `max_depth`（默认 10）限制遍历深度
- 自环由数据库触发器 `trg_edge_no_self_loop` 直接拒绝，应用层另有一道 BFS 环检测

## 验证

```bash
pytest tests/test_lineage.py -q      # 10 passed，含性能守护
pytest tests/test_lineage.py -k performance
```

性能断言：`test_impact_query_performance` 要求单次查询 <1.0s，实测 ~6ms。

## 后续优化（若数据量到 50 万）

1. 为 `lineage_edge(upstream_id)` 加覆盖索引（已建）
2. 高频查询节点加 60s 内存缓存，边变更时失效
3. 超大规模改用图数据库（Neo4j），但当前规模下是过度设计
