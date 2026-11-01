"""血缘节点注册、影响面查询与图导出。"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from g05.core.config import settings
from g05.core.logging import get_logger, log_event
from g05.models.lineage import LineageEdge, LineageNode

log = get_logger("lineage.impact")


@dataclass
class ImpactNode:
    """影响面中的一个节点及其可解释路径。"""

    id: int
    node_type: str
    node_key: str
    node_name: str
    owner: str | None
    depth: int
    path: list[str] = field(default_factory=list)


@dataclass
class ImpactResult:
    """影响面查询结果。"""

    root: ImpactNode | None
    direction: str
    nodes: list[ImpactNode]
    truncated: bool
    cycles: list[list[str]]
    edges: list[dict[str, int]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.nodes)

    def by_type(self) -> dict[str, list[ImpactNode]]:
        grouped: dict[str, list[ImpactNode]] = {}
        for node in self.nodes:
            grouped.setdefault(node.node_type, []).append(node)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.__dict__ if self.root else None,
            "direction": self.direction,
            "total": self.total,
            "truncated": self.truncated,
            "cycles": self.cycles,
            "edges": self.edges,
            "nodes": [node.__dict__ for node in self.nodes],
            "summary": {node_type: len(nodes) for node_type, nodes in self.by_type().items()},
        }


@dataclass(frozen=True)
class _FrontierNode:
    """BFS 队首元素；保留 id 路径才能区分真环与菱形汇聚。"""

    node_id: int
    depth: int
    path_keys: tuple[str, ...]
    path_ids: frozenset[int]


def _to_node(row: LineageNode, depth: int, path: Iterable[str]) -> ImpactNode:
    return ImpactNode(
        id=row.id,
        node_type=row.node_type,
        node_key=row.node_key,
        node_name=row.node_name,
        owner=row.owner,
        depth=depth,
        path=list(path),
    )


def _find_cycles(
    edges: Iterable[dict[str, int]],
    key_by_id: Mapping[int, str],
    root_id: int,
) -> list[list[str]]:
    """在已遍历子图上用迭代 DFS 找回边，避免单一路径 BFS 漏报环。"""
    adjacency: dict[int, set[int]] = {node_id: set() for node_id in key_by_id}
    for edge in edges:
        source_id = edge["source"]
        target_id = edge["target"]
        if source_id in adjacency and target_id in key_by_id:
            adjacency[source_id].add(target_id)

    state: dict[int, int] = {node_id: 0 for node_id in key_by_id}
    cycles: list[list[str]] = []
    signatures: set[tuple[int, ...]] = set()
    starts = [root_id, *sorted(node_id for node_id in key_by_id if node_id != root_id)]

    for start in starts:
        if state[start] != 0:
            continue
        state[start] = 1
        path = [start]
        path_position = {start: 0}
        stack: list[tuple[int, Any]] = [(start, iter(sorted(adjacency[start])))]

        while stack:
            current, neighbors = stack[-1]
            try:
                target = next(neighbors)
            except StopIteration:
                stack.pop()
                state[current] = 2
                path_position.pop(current, None)
                path.pop()
                continue

            if state[target] == 0:
                state[target] = 1
                path_position[target] = len(path)
                path.append(target)
                stack.append((target, iter(sorted(adjacency[target]))))
                continue
            if state[target] == 1:
                cycle_ids = (*path[path_position[target] :], target)
                if cycle_ids not in signatures:
                    signatures.add(cycle_ids)
                    cycles.append([key_by_id[node_id] for node_id in cycle_ids])

    return cycles


def resolve_node(session: Session, node_type: str, node_key: str) -> LineageNode | None:
    stmt = select(LineageNode).where(
        LineageNode.node_type == node_type,
        LineageNode.node_key == node_key,
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
    """逐层 BFS 计算上游或下游影响面。

    每层只批量查询一次边和一次节点。节点在入队时加入 ``visited``，
    所以菱形依赖不会重复计数；只有回到当前路径中的祖先才报告为环。
    """
    if direction not in {"downstream", "upstream"}:
        raise ValueError(f"不支持的血缘方向: {direction}")

    depth_limit = max_depth if max_depth is not None else settings.get("app.lineage.max_depth", 10)
    max_nodes = settings.get("app.lineage.max_nodes", 5000)
    detect_cycle = settings.get("app.lineage.detect_cycle", True)
    warn_ms = settings.get("app.lineage.warn_ms", 1000)
    if depth_limit < 0:
        raise ValueError("max_depth 不能小于 0")
    if max_nodes < 1:
        raise ValueError("app.lineage.max_nodes 必须大于 0")

    started = time.perf_counter()
    root_row = resolve_node(session, node_type, node_key)
    if root_row is None:
        return ImpactResult(
            root=None,
            direction=direction,
            nodes=[],
            truncated=False,
            cycles=[],
        )

    root = _to_node(root_row, 0, [root_row.node_key])
    if direction == "downstream":
        source_column = LineageEdge.upstream_id
        target_column = LineageEdge.downstream_id
    else:
        source_column = LineageEdge.downstream_id
        target_column = LineageEdge.upstream_id

    visited: set[int] = {root_row.id}
    key_by_id: dict[int, str] = {root_row.id: root_row.node_key}
    results: list[ImpactNode] = []
    graph_edges: list[dict[str, int]] = []
    edge_signatures: set[tuple[int, int]] = set()
    frontier = [
        _FrontierNode(
            node_id=root_row.id,
            depth=0,
            path_keys=(root_row.node_key,),
            path_ids=frozenset({root_row.id}),
        )
    ]
    traversed_layers = 0
    truncated = False

    while frontier and frontier[0].depth < depth_limit and not truncated:
        traversed_layers += 1
        frontier_ids = [item.node_id for item in frontier]
        edge_rows = session.execute(
            select(source_column, target_column)
            .where(source_column.in_(frontier_ids))
            .order_by(source_column, target_column)
        ).all()
        targets_by_source: dict[int, list[int]] = {node_id: [] for node_id in frontier_ids}
        candidate_ids: set[int] = set()
        for source_id, target_id in edge_rows:
            targets_by_source[source_id].append(target_id)
            if target_id not in visited:
                candidate_ids.add(target_id)

        rows_by_id: dict[int, LineageNode] = {}
        if candidate_ids:
            node_rows = session.execute(
                select(LineageNode)
                .where(LineageNode.id.in_(candidate_ids))
                .order_by(LineageNode.id)
            ).scalars()
            rows_by_id = {row.id: row for row in node_rows}

        next_frontier: list[_FrontierNode] = []
        for current in frontier:
            for target_id in targets_by_source[current.node_id]:
                edge_signature = (current.node_id, target_id)
                if target_id in current.path_ids:
                    if edge_signature not in edge_signatures:
                        graph_edges.append({"source": current.node_id, "target": target_id})
                        edge_signatures.add(edge_signature)
                    continue

                if target_id in visited:
                    if edge_signature not in edge_signatures:
                        graph_edges.append({"source": current.node_id, "target": target_id})
                        edge_signatures.add(edge_signature)
                    continue

                row = rows_by_id.get(target_id)
                if row is None:
                    continue
                if len(results) >= max_nodes:
                    truncated = True
                    break

                new_path_keys = (*current.path_keys, row.node_key)
                new_path_ids = current.path_ids | {row.id}
                visited.add(row.id)
                key_by_id[row.id] = row.node_key
                results.append(_to_node(row, current.depth + 1, new_path_keys))
                next_frontier.append(
                    _FrontierNode(
                        node_id=row.id,
                        depth=current.depth + 1,
                        path_keys=new_path_keys,
                        path_ids=new_path_ids,
                    )
                )
                if edge_signature not in edge_signatures:
                    graph_edges.append({"source": current.node_id, "target": row.id})
                    edge_signatures.add(edge_signature)
            if truncated:
                break
        frontier = next_frontier

    cycles = _find_cycles(graph_edges, key_by_id, root_row.id) if detect_cycle else []
    elapsed_ms = (time.perf_counter() - started) * 1000
    fields = {
        "root": f"{node_type}:{node_key}",
        "direction": direction,
        "total": len(results),
        "cycles": len(cycles),
        "layers": traversed_layers,
        "truncated": truncated,
        "elapsed_ms": round(elapsed_ms, 3),
    }
    log_event(log, "lineage.impact", **fields)
    if elapsed_ms >= warn_ms:
        log.warning("lineage.impact.slow", extra={"extra_fields": fields})

    return ImpactResult(
        root=root,
        direction=direction,
        nodes=results,
        truncated=truncated,
        cycles=cycles,
        edges=graph_edges,
    )


def register_node(
    session: Session,
    *,
    node_type: str,
    node_key: str,
    node_name: str,
    owner: str | None = None,
) -> LineageNode:
    """幂等注册节点；重复业务键只更新展示信息。"""
    existing = resolve_node(session, node_type, node_key)
    if existing:
        if node_name and existing.node_name != node_name:
            existing.node_name = node_name
        if owner is not None:
            existing.owner = owner
        return existing
    node = LineageNode(
        node_type=node_type,
        node_key=node_key,
        node_name=node_name,
        owner=owner,
    )
    session.add(node)
    session.flush()
    return node


def register_nodes(
    session: Session,
    nodes: Iterable[Mapping[str, Any]],
) -> list[LineageNode]:
    """批量幂等注册节点，事务提交由调用方统一控制。"""
    return [register_node(session, **dict(node)) for node in nodes]


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
    """幂等注册依赖边；自环仍由数据库触发器最终拒绝。"""
    upstream = resolve_node(session, upstream_type, upstream_key)
    downstream = resolve_node(session, downstream_type, downstream_key)
    if upstream is None or downstream is None:
        raise ValueError(
            "节点不存在: "
            f"{upstream_type}:{upstream_key} / {downstream_type}:{downstream_key}"
        )

    field_predicate = (
        LineageEdge.field_name.is_(None)
        if field_name is None
        else LineageEdge.field_name == field_name
    )
    stmt = select(LineageEdge).where(
        LineageEdge.upstream_id == upstream.id,
        LineageEdge.downstream_id == downstream.id,
        field_predicate,
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing:
        return existing

    edge = LineageEdge(
        upstream_id=upstream.id,
        downstream_id=downstream.id,
        field_name=field_name,
        relation=relation,
    )
    session.add(edge)
    session.flush()
    return edge


def register_edges(
    session: Session,
    edges: Iterable[Mapping[str, Any]],
) -> list[LineageEdge]:
    """批量幂等注册依赖边，事务提交由调用方统一控制。"""
    return [register_edge(session, **dict(edge)) for edge in edges]


def build_graph(session: Session, limit: int = 500) -> dict[str, list[dict[str, Any]]]:
    """导出给前端使用的全图。"""
    nodes = list(
        session.execute(select(LineageNode).order_by(LineageNode.id).limit(limit)).scalars()
    )
    node_ids = [node.id for node in nodes]
    edges: list[dict[str, Any]] = []
    if node_ids:
        edge_rows = session.execute(
            select(LineageEdge)
            .where(
                LineageEdge.upstream_id.in_(node_ids),
                LineageEdge.downstream_id.in_(node_ids),
            )
            .order_by(LineageEdge.upstream_id, LineageEdge.downstream_id)
        ).scalars()
        edges = [
            {
                "source": edge.upstream_id,
                "target": edge.downstream_id,
                "field": edge.field_name,
                "relation": edge.relation,
            }
            for edge in edge_rows
        ]
    return {
        "nodes": [
            {
                "id": node.id,
                "type": node.node_type,
                "key": node.node_key,
                "name": node.node_name,
                "owner": node.owner,
            }
            for node in nodes
        ],
        "edges": edges,
    }
