"""血缘节点注册、影响面查询与图导出。"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
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
    """影响面查询结果。

    截断语义按 A 的裁定（2026-11-01，方案 2）：
    ``total`` 是**完整可达节点数**（不含 root，截断与否都如实计数），
    ``returned`` 是本次实际返回的节点数；两者之差按类型记入 ``omitted_by_type``，
    被谁截断记入 ``truncated_by``（``max_depth`` / ``max_nodes`` 的子集）。
    """

    root: ImpactNode | None
    direction: str
    nodes: list[ImpactNode]
    truncated: bool
    cycles: list[list[str]]
    total: int = 0
    returned: int = 0
    truncated_by: list[str] = field(default_factory=list)
    omitted_by_type: dict[str, int] = field(default_factory=dict)
    # 仅内部使用（环检测与调试），按裁定不进入 /impact 响应体
    edges: list[dict[str, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 兜底：未显式给出计数时，按"未截断"处理，避免出现 total=0 的假数据
        if not self.returned:
            self.returned = len(self.nodes)
        if not self.total:
            self.total = self.returned

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
            "returned": self.returned,
            "truncated": self.truncated,
            "truncated_by": self.truncated_by,
            "omitted_by_type": self.omitted_by_type,
            "cycles": self.cycles,
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
                ring = cycle_ids[:-1]
                canonical_ring = min(ring[index:] + ring[:index] for index in range(len(ring)))
                cycle_ids = (*canonical_ring, canonical_ring[0])
                if cycle_ids not in signatures:
                    signatures.add(cycle_ids)
                    cycles.append([key_by_id[node_id] for node_id in cycle_ids])

    return cycles


def _collect_reachable(
    session: Session,
    source_column: Any,
    target_column: Any,
    root_id: int,
) -> tuple[dict[int, str], int]:
    """只收集 id 与类型、不建路径的计数遍历。

    按 A 的裁定用于"诚实截断"：主遍历被 ``max_nodes`` 截断、或被 ``max_depth``
    限制时，用这一遍给出完整可达节点数。因此它**忽略** ``max_nodes`` 与
    ``max_depth`` 两个上限，只保留 ``visited`` 防环。
    验收规模（≤200 节点）下开销可忽略；规模上到几千节点后应改为分页或预聚合，
    见 ADR-004 "未来工作"。

    返回 ``(id -> node_type, 达到的最大深度)``，不含 root。
    """
    seen: set[int] = {root_id}
    type_by_id: dict[int, str] = {}
    frontier: list[int] = [root_id]
    depth = 0
    max_depth_reached = 0

    while frontier:
        depth += 1
        rows = session.execute(select(source_column, target_column).where(source_column.in_(frontier))).all()
        pending: list[int] = []
        for _source_id, target_id in rows:
            if target_id in seen:
                continue
            seen.add(target_id)
            pending.append(target_id)
        if not pending:
            break
        type_rows = session.execute(
            select(LineageNode.id, LineageNode.node_type)
            .where(LineageNode.id.in_(pending))
            .order_by(LineageNode.id)
        ).all()
        for node_id, node_type in type_rows:
            type_by_id[node_id] = node_type
        max_depth_reached = depth
        # 悬空边（边指向不存在的节点）不再展开，避免反复查询同一批 id
        frontier = [node_id for node_id in pending if node_id in type_by_id]

    return type_by_id, max_depth_reached


def _truncation_metadata(
    *,
    session: Session,
    source_column: Any,
    target_column: Any,
    root_id: int,
    results: list[ImpactNode],
    depth_limit: int,
    frontier: list[_FrontierNode],
    hit_max_nodes: bool,
) -> tuple[int, int, list[str], dict[str, int]]:
    """按 A 的裁定计算 ``total / returned / truncated_by / omitted_by_type``。

    只有"确实被截断"时才付第二遍计数的代价：``max_nodes`` 命中必然要计数；
    深度受限时先用一条探针确认还有边，避免叶子图白跑一遍。
    """
    returned = len(results)
    depth_limited = bool(frontier) and frontier[0].depth >= depth_limit
    needs_count = hit_max_nodes
    if not needs_count and depth_limited:
        already_returned = {root_id, *(node.id for node in results)}
        probe = session.execute(
            select(target_column)
            .where(
                source_column.in_([item.node_id for item in frontier]),
                target_column.not_in(already_returned),
            )
            .limit(1)
        ).first()
        needs_count = probe is not None
    if not needs_count:
        return returned, returned, [], {}

    type_by_id, max_depth_reached = _collect_reachable(session, source_column, target_column, root_id)
    total = len(type_by_id)
    if total <= returned:
        # 探针只命中了回边（目标已访问），实际没有遗漏
        return returned, returned, [], {}

    returned_ids = {node.id for node in results}
    omitted: dict[str, int] = {}
    for node_id in sorted(type_by_id):
        if node_id in returned_ids:
            continue
        node_type = type_by_id[node_id]
        omitted[node_type] = omitted.get(node_type, 0) + 1

    truncated_by: list[str] = []
    if max_depth_reached > depth_limit:
        truncated_by.append("max_depth")
    if hit_max_nodes:
        truncated_by.append("max_nodes")
    return total, returned, truncated_by, omitted


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

    返回值带诚实截断元数据（A 裁定 2026-11-01 决定 1）：
    ``total`` 为完整可达节点数（不含 root），``returned`` 为实际返回数，
    ``truncated_by`` 说明被 ``max_depth`` 还是 ``max_nodes`` 截断，
    ``omitted_by_type`` 给出未返回节点按类型的计数。
    """
    if direction not in {"downstream", "upstream"}:
        raise ValueError(f"不支持的血缘方向: {direction}")

    configured_depth_limit = settings.get("app.lineage.max_depth", 10)
    if max_depth is not None and max_depth > configured_depth_limit:
        raise ValueError("max_depth cannot exceed app.lineage.max_depth " f"({configured_depth_limit})")
    depth_limit = configured_depth_limit if max_depth is None else max_depth
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
                select(LineageNode).where(LineageNode.id.in_(candidate_ids)).order_by(LineageNode.id)
            ).scalars()
            rows_by_id = {row.id: row for row in node_rows}

        next_frontier: list[_FrontierNode] = []
        for current in frontier:
            for target_id in targets_by_source[current.node_id]:
                # Upstream queries reverse traversal, but response edges keep the
                # real data-flow orientation: upstream -> downstream.
                edge_signature = (
                    (current.node_id, target_id)
                    if direction == "downstream"
                    else (target_id, current.node_id)
                )
                if target_id in current.path_ids:
                    if edge_signature not in edge_signatures:
                        graph_edges.append({"source": edge_signature[0], "target": edge_signature[1]})
                        edge_signatures.add(edge_signature)
                    continue

                if target_id in visited:
                    if edge_signature not in edge_signatures:
                        graph_edges.append({"source": edge_signature[0], "target": edge_signature[1]})
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
                    graph_edges.append({"source": edge_signature[0], "target": edge_signature[1]})
                    edge_signatures.add(edge_signature)
            if truncated:
                break
        frontier = next_frontier

    cycles = _find_cycles(graph_edges, key_by_id, root_row.id) if detect_cycle else []
    total, returned, truncated_by, omitted_by_type = _truncation_metadata(
        session=session,
        source_column=source_column,
        target_column=target_column,
        root_id=root_row.id,
        results=results,
        depth_limit=depth_limit,
        frontier=frontier,
        hit_max_nodes=truncated,
    )
    truncated = total > returned

    elapsed_ms = (time.perf_counter() - started) * 1000
    fields = {
        "root": f"{node_type}:{node_key}",
        "direction": direction,
        "total": total,
        "returned": returned,
        "truncated": truncated,
        "truncated_by": truncated_by,
        "cycles": len(cycles),
        "layers": traversed_layers,
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
        total=total,
        returned=returned,
        truncated_by=truncated_by,
        omitted_by_type=omitted_by_type,
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
            "节点不存在: " f"{upstream_type}:{upstream_key} / {downstream_type}:{downstream_key}"
        )

    field_predicate = (
        LineageEdge.field_name.is_(None) if field_name is None else LineageEdge.field_name == field_name
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


def build_graph(session: Session, limit: int = 500) -> dict[str, Any]:
    """导出给前端使用的全图。

    按 A 的裁定（决定 2，方案 1）保留 induced-graph 语义（边两端都在返回节点集内），
    另加 ``total`` / ``returned`` / ``truncated``，让前端不再把前 N 个节点说成"全图"。
    不引入 cursor 分页；超过 1000 节点再启用，见 ADR-004 "未来工作"。
    """
    total = int(session.execute(select(func.count()).select_from(LineageNode)).scalar_one())
    nodes = list(session.execute(select(LineageNode).order_by(LineageNode.id).limit(limit)).scalars())
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
    returned = len(nodes)
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
        "total": total,
        "returned": returned,
        "truncated": total > returned,
    }
