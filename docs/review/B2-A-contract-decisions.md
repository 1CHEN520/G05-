# B2 lineage contract decisions for A

This note records contract changes intentionally deferred from the B2 correctness
patch. No item below is implemented in the current response schema.

## Decision 1: honest truncation metadata

Current `ImpactResult.total` is the number of returned nodes. When `max_nodes` or
`max_depth` stops traversal, clients cannot determine the true total or why the
result is incomplete.

Options for A:

1. Keep `total` as returned count and add `returned`, `has_more`, and
   `depth_limited`. This is additive but leaves the name `total` ambiguous.
2. Redefine `total` as the complete reachable count and add `returned` plus an
   omitted-by-type summary. This is clearer but requires an additional bounded
   traversal/count strategy and coordinated C3/C4 updates.

Affected consumers: `schemas/dto.py`, `/api/lineage/impact`, C3 integrations,
and `web/js/lineage.js`.

## Decision 2: graph pagination

`/api/lineage/graph` currently returns only the first `limit` nodes and induced
edges, without `total` or `truncated` metadata. The UI calls `limit=300` and labels
the result as the full graph.

Options for A:

1. Add `total`, `returned`, and `truncated`, retaining the current induced graph.
2. Add cursor pagination (`after_id`) and define how cross-page edges are exposed.

Affected consumers: graph response DTO, API documentation, and C4 visualization.

## Decision 3: frozen DTO alignment

`ImpactOut` does not currently include the `edges` consumed by the visualization,
and lineage routes do not declare response models. Adding `edges` or new truncation
fields requires A approval because the DTO is a shared boundary.

---

# A 的裁定（2026-11-01，陈锦妍）

三条都裁了，**按本节实现即可，不用再等确认**。改 `schemas/dto.py` 的部分由我来做，见决定 3。

## 决定 1（诚实截断）：选方案 2

`ImpactResult.total` 语义改为 **完整可达节点数（不含 root）**，另加三个字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `total` | int | 完整可达节点数（截断与否都如实计数） |
| `returned` | int | 本次实际返回的节点数 |
| `truncated` | bool | 是否发生截断 |
| `truncated_by` | list[str] | `"max_depth"` / `"max_nodes"` 的子集，说明被谁截断 |
| `omitted_by_type` | dict[str,int] | 未返回节点按类型计数（C4 可以直接渲染） |

理由：
- 验收标准（issue #28）写的是"超限如实说明被截断，不许静默丢弃"，方案 1 留下语义含糊的 `total` 迟早有人踩。
- **现在没有任何消费者上线**（C3、C4 都还没合并），改语义的代价窗口就是此刻，越晚越贵。
- 计数实现：对被 `max_nodes`/`max_depth` 截断的情形，跑第二遍"只收集 id、不建 path"的 BFS 来计数，图规模在验收范围内（≤200 节点）性能无虞；将来真到几千节点再谈分页。

## 决定 2（图接口分页）：选方案 1

`/api/lineage/graph` 加 `total`、`returned`、`truncated` 三个字段，保持现有 induced-graph 语义；**不引入 cursor**。

理由：前端一页最多 300 节点，cursor 分页会让"跨页的边归谁"变成 C4 渲染的额外负担，当前规模下收益为负。cursor 方案记入 ADR-004 的"未来工作"，超过 1000 节点再启用。

## 决定 3（DTO 对齐）：部分批准

- `/impact` 的响应**不加 `edges`**——图接口已经有边，双份传输只给 C4 添乱；影响面页按"节点 + 路径 + 环"展示。
- `/graph` 的响应模型**含 `edges`**。
- 但三条 lineage 路由都必须挂 `response_model`，这是接口文档的底线。DTO 我来补：`schemas/dto.py` 会新增 `ImpactOut` / `GraphOut`（含上面五个截断字段），**你 rebase 之后把路由挂上即可，DTO 本身不要再改**——改了要重新走这条流程。

## 附：你这次分支的核验结果（2026-11-01）

在干净环境实测了 `feat/b2-lineage`（commit 8a7e4d7）：

1. **单独跑 `pytest` 收集失败**：`ModuleNotFoundError: No module named 'g05.models'`。
   这**不是你的问题**——`models/lineage.py` 是 B1 的文件，合并顺序本来就是 B1 → C3 → B2。
2. **手动补入 B1 的模型后**：10 passed / 16 error，错误全是 `fixture 'db' not found`。
   `conftest.py` 归 C5（issue #5 的交付物），`db` 夹具要等 C5 合并。
3. **结论：代码本体我没发现方向性问题**，但你的 PR 请**等 B1 和 C5 合并后再 rebase 提上来**，或者现在就开并标成 Draft。现在开普通 PR，CI 和我这边都过不了，白占评审队列。
4. 两处超出你清单的文件（`docs/review/`、`tests/test_lineage_validation.py`）我都收了——测试多写是好事；下次这类"清单外新增"先在 issue 里说一声即可。

> 另：这份 `docs/review/B2-A-contract-decisions.md` 已由 A 收进 main，后续契约裁定都记在这里，谁改 DTO 谁留痕。
