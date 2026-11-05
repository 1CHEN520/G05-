"""血缘影响面查询。

改名影响面 = 给定测点，向下游 BFS，找出所有直接/间接依赖它的报表、任务、接口。

性能设计（硬指标 <1 秒）：
- 逐层 BFS，每层一条 ``IN`` 查询，避免 N+1
- ``(upstream_id)`` / ``(downstream_id)`` 均有索引
- 深度上限来自配置（默认 10），防环导致无限循环
- 结果集上限保护，超出则截断并标记 truncated
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from g05.core.config import settings
from g05.core.logging import get_logger, log_event
from g05.models.lineage import LineageEdge, LineageNode

log = get_logger("lineage.impact")


@dataclass
class ImpactNode:
    id: int
    node_type: str
    node_key: str
    node_name: str
    owner: str | None
    depth: int
    # 到达该节点的路径（node_key 序列），用于解释"为什么受影响"
    path: list[str] = field(default_factory=list)


@dataclass
class ImpactResult:
    root: ImpactNode | None
    direction: str
    nodes: list[ImpactNode]
    truncated: bool
    cycles: list[list[str]]

    @property
    def total(self) -> int:
        return len(self.nodes)

    def by_type(self) -> dict[str, list[ImpactNode]]:
        grouped: dict[str, list[ImpactNode]] = {}
        for node in self.nodes:
            grouped.setdefault(node.node_type, []).append(node)
        return grouped

    def to_dict(self) -> dict:
        return {
            "root": self.root.__dict__ if self.root else None,
            "direction": self.direction,
            "total": len(self.nodes),
            "truncated": self.truncated,
            "cycles": self.cycles,
            "nodes": [n.__dict__ for n in self.nodes],
            "summary": {k: len(v) for k, v in self.by_type().items()},
        }


def _to_node(row: LineageNode, depth: int, path: list[str]) -> ImpactNode:
    return ImpactNode(
        id=row.id,
        node_type=row.node_type,
        node_key=row.node_key,
        node_name=row.node_name,
        owner=row.owner,
        depth=depth,
        path=path,
    )


def resolve_node(session: Session, node_type: str, node_key: str) -> LineageNode | None:
    stmt = select(LineageNode).where(
        LineageNode.node_type == node_type, LineageNode.node_key == node_key
    )
    return session.execute(stmt).scalar_one_or_none()


def impact_of(
    session: Session,
    *,
    node_type: str = "point",
    node_key: str,
    direction: str = "downstream",
    max_depth: int | None = None,
) -> ImpactResult:
    """计算影响面。

    Args:
        direction: ``downstream`` 查"我影响谁"，``upstream`` 查"谁影响我"
    """
    max_depth = max_depth or settings.get("app.lineage.max_depth", 10)
    max_nodes = settings.get("app.lineage.max_nodes", 5000)

    root_row = resolve_node(session, node_type, node_key)
    if root_row is None:
        return ImpactResult(root=None, direction=direction, nodes=[], truncated=False, cycles=[])

    root = _to_node(root_row, 0, [root_row.node_key])

    # 选择遍历方向：downstream 沿 upstream_id -> downstream_id
    if direction == "downstream":
        src_col, dst_col = LineageEdge.upstream_id, LineageEdge.downstream_id
    else:
        src_col, dst_col = LineageEdge.downstream_id, LineageEdge.upstream_id

    visited: set[int] = {root_row.id}
    results: list[ImpactNode] = []
    cycles: list[list[str]] = []
    truncated = False

    queue: deque[tuple[LineageNode, int, list[str]]] = deque([(root_row, 0, [root_row.node_key])])

    while queue:
        current, depth, path = queue.popleft()
        if depth >= max_depth:
            continue

        # 一层一条查询，拿到所有下游边
        edge_stmt = select(dst_col).where(src_col == current.id)
        next_ids = [r for r in session.execute(edge_stmt).scalars() if r not in visited]
        if not next_ids:
            continue

        node_rows = list(
            session.execute(select(LineageNode).where(LineageNode.id.in_(next_ids))).scalars()
        )

        for row in node_rows:
            new_path = [*path, row.node_key]
            if row.id in visited:
                # 回边 = 环
                if settings.get("app.lineage.detect_cycle", True):
                    cycles.append(new_path)
                continue

            visited.add(row.id)
            results.append(_to_node(row, depth + 1, new_path))
            queue.append((row, depth + 1, new_path))

            if len(results) >= max_nodes:
                truncated = True
                queue.clear()
                break

    log_event(
        log,
        "lineage.impact",
        root=f"{node_type}:{node_key}",
        direction=direction,
        total=len(results),
        cycles=len(cycles),
    )
    return ImpactResult(root=root, direction=direction, nodes=results, truncated=truncated, cycles=cycles)


def register_node(
    session: Session,
    *,
    node_type: str,
    node_key: str,
    node_name: str,
    owner: str | None = None,
) -> LineageNode:
    """注册节点（幂等：已存在则返回）。"""
    existing = resolve_node(session, node_type, node_key)
    if existing:
        if node_name and existing.node_name != node_name:
            existing.node_name = node_name
        if owner:
            existing.owner = owner
        return existing
    node = LineageNode(node_type=node_type, node_key=node_key, node_name=node_name, owner=owner)
    session.add(node)
    session.flush()
    return node


def register_edge(
    session: Session,
    *,
    upstream_key: str,
    downstream_key: str,
    upstream_type: str = "point",
    downstream_type: str = "report",
    field_name: str | None = None,
    relation: str = "read",
) -> LineageEdge:
    """注册依赖边（幂等）。自环与重复边由唯一约束/触发器拦截。"""
    up = resolve_node(session, upstream_type, upstream_key)
    down = resolve_node(session, downstream_type, downstream_key)
    if up is None or down is None:
        raise ValueError(f"节点不存在: {upstream_type}:{upstream_key} / {downstream_type}:{downstream_key}")

    stmt = select(LineageEdge).where(
        LineageEdge.upstream_id == up.id,
        LineageEdge.downstream_id == down.id,
        LineageEdge.field_name.is_(field_name) if field_name is None else LineageEdge.field_name == field_name,
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing:
        return existing

    edge = LineageEdge(
        upstream_id=up.id,
        downstream_id=down.id,
        field_name=field_name,
        relation=relation,
    )
    session.add(edge)
    session.flush()
    return edge


def build_graph(session: Session, limit: int = 500) -> dict:
    """导出全图（供可视化），返回 nodes + edges 的字典。"""
    nodes = list(session.execute(select(LineageNode).limit(limit)).scalars())
    node_ids = [n.id for n in nodes]
    edges: list[dict] = []
    if node_ids:
        rows = session.execute(
            select(LineageEdge).where(
                LineageEdge.upstream_id.in_(node_ids), LineageEdge.downstream_id.in_(node_ids)
            )
        ).scalars()
        edges = [
            {
                "source": e.upstream_id,
                "target": e.downstream_id,
                "field": e.field_name,
                "relation": e.relation,
            }
            for e in rows
        ]
    return {
        "nodes": [
            {
                "id": n.id,
                "type": n.node_type,
                "key": n.node_key,
                "name": n.node_name,
                "owner": n.owner,
            }
            for n in nodes
        ],
        "edges": edges,
    }
