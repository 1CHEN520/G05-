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
