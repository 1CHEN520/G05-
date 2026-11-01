"""血缘影响面测试：方向、路径、环、截断与查询性能。"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DatabaseError

from g05.core.config import settings
from g05.lineage.impact import (
    build_graph,
    impact_of,
    register_edge,
    register_edges,
    register_node,
    register_nodes,
)
from g05.models.lineage import LineageEdge, LineageNode


@pytest.fixture()
def graph(db):
    """构造 point A/B -> dataset D -> report R / task T。"""
    register_nodes(
        db,
        [
            {"node_type": "point", "node_key": "P_A", "node_name": "测点A", "owner": "设备部"},
            {"node_type": "point", "node_key": "P_B", "node_name": "测点B", "owner": "设备部"},
            {
                "node_type": "dataset",
                "node_key": "DS_1",
                "node_name": "清洗宽表",
                "owner": "数据组",
            },
            {"node_type": "report", "node_key": "RPT_1", "node_name": "OEE日报", "owner": "生产部"},
            {"node_type": "task", "node_key": "TASK_1", "node_name": "特征计算", "owner": "算法组"},
        ],
    )
    register_edges(
        db,
        [
            {
                "upstream_key": "P_A",
                "downstream_key": "DS_1",
                "upstream_type": "point",
                "downstream_type": "dataset",
            },
            {
                "upstream_key": "P_B",
                "downstream_key": "DS_1",
                "upstream_type": "point",
                "downstream_type": "dataset",
            },
            {
                "upstream_key": "DS_1",
                "downstream_key": "RPT_1",
                "upstream_type": "dataset",
                "downstream_type": "report",
            },
            {
                "upstream_key": "DS_1",
                "downstream_key": "TASK_1",
                "upstream_type": "dataset",
                "downstream_type": "task",
            },
        ],
    )
    db.commit()
    return db


def test_node_and_edge_registration_are_idempotent(graph):
    node = register_node(
        graph,
        node_type="point",
        node_key="P_A",
        node_name="测点A（更新）",
        owner="设备部",
    )
    edge = register_edge(
        graph,
        upstream_key="P_A",
        downstream_key="DS_1",
        upstream_type="point",
        downstream_type="dataset",
    )
    graph.commit()

    node_count = graph.scalar(
        select(func.count()).select_from(LineageNode).where(LineageNode.node_key == "P_A")
    )
    edge_count = graph.scalar(
        select(func.count())
        .select_from(LineageEdge)
        .where(
            LineageEdge.upstream_id == edge.upstream_id,
            LineageEdge.downstream_id == edge.downstream_id,
        )
    )
    assert node.node_name == "测点A（更新）"
    assert node_count == 1
    assert edge_count == 1


def test_downstream_transitive_with_complete_paths(graph):
    result = impact_of(graph, node_type="point", node_key="P_A", direction="downstream")
    by_key = {node.node_key: node for node in result.nodes}

    assert set(by_key) == {"DS_1", "RPT_1", "TASK_1"}
    assert by_key["DS_1"].depth == 1
    assert by_key["RPT_1"].depth == 2
    assert by_key["RPT_1"].path == ["P_A", "DS_1", "RPT_1"]
    assert result.to_dict()["summary"] == {"dataset": 1, "report": 1, "task": 1}


def test_upstream_direction(graph):
    result = impact_of(graph, node_type="report", node_key="RPT_1", direction="upstream")
    assert {node.node_key for node in result.nodes} == {"DS_1", "P_A", "P_B"}


def test_max_depth_limits_traversal(graph):
    result = impact_of(
        graph,
        node_type="point",
        node_key="P_A",
        direction="downstream",
        max_depth=1,
    )
    assert {node.node_key for node in result.nodes} == {"DS_1"}


def test_zero_depth_returns_only_root(graph):
    result = impact_of(graph, node_type="point", node_key="P_A", max_depth=0)
    assert result.root is not None
    assert result.nodes == []


def test_missing_node_returns_empty(graph):
    result = impact_of(graph, node_type="point", node_key="NOT_EXIST")
    assert result.root is None
    assert result.nodes == []


def test_invalid_direction_rejected(graph):
    with pytest.raises(ValueError, match="方向"):
        impact_of(graph, node_type="point", node_key="P_A", direction="sideways")


def test_two_node_cycle_is_reported_without_looping(graph):
    register_edge(
        graph,
        upstream_key="DS_1",
        downstream_key="P_A",
        upstream_type="dataset",
        downstream_type="point",
    )
    graph.commit()

    result = impact_of(graph, node_type="point", node_key="P_A", direction="downstream")

    assert ["P_A", "DS_1", "P_A"] in result.cycles
    assert {node.node_key for node in result.nodes} == {"DS_1", "RPT_1", "TASK_1"}


def test_diamond_dependency_is_not_a_cycle(graph):
    register_node(graph, node_type="dataset", node_key="DS_2", node_name="另一张宽表")
    register_edge(
        graph,
        upstream_key="P_A",
        downstream_key="DS_2",
        upstream_type="point",
        downstream_type="dataset",
    )
    register_edge(
        graph,
        upstream_key="DS_2",
        downstream_key="RPT_1",
        upstream_type="dataset",
        downstream_type="report",
    )
    graph.commit()

    result = impact_of(graph, node_type="point", node_key="P_A")

    assert result.cycles == []
    assert [node.node_key for node in result.nodes].count("RPT_1") == 1


def test_cycle_is_found_when_bfs_first_reaches_node_by_another_path(graph):
    """BFS 选中的解释路径不含环节点时，子图检测仍要发现真实环。"""
    register_nodes(
        graph,
        [
            {"node_type": "dataset", "node_key": "DS_C", "node_name": "先访问路径"},
            {"node_type": "dataset", "node_key": "DS_B", "node_name": "环入口"},
            {"node_type": "task", "node_key": "TASK_D", "node_name": "汇聚节点"},
        ],
    )
    register_edges(
        graph,
        [
            {
                "upstream_key": "P_A",
                "downstream_key": "DS_C",
                "upstream_type": "point",
                "downstream_type": "dataset",
            },
            {
                "upstream_key": "P_A",
                "downstream_key": "DS_B",
                "upstream_type": "point",
                "downstream_type": "dataset",
            },
            {
                "upstream_key": "DS_C",
                "downstream_key": "TASK_D",
                "upstream_type": "dataset",
                "downstream_type": "task",
            },
            {
                "upstream_key": "DS_B",
                "downstream_key": "TASK_D",
                "upstream_type": "dataset",
                "downstream_type": "task",
            },
            {
                "upstream_key": "TASK_D",
                "downstream_key": "DS_B",
                "upstream_type": "task",
                "downstream_type": "dataset",
            },
        ],
    )
    graph.commit()

    result = impact_of(graph, node_type="point", node_key="P_A")

    assert ["DS_B", "TASK_D", "DS_B"] in result.cycles


def test_result_truncation_is_explicit(graph, monkeypatch):
    original_get = settings.get

    def limited_get(key, default=None):
        return 2 if key == "app.lineage.max_nodes" else original_get(key, default)

    monkeypatch.setattr(settings, "get", limited_get)
    result = impact_of(graph, node_type="point", node_key="P_A")

    assert result.truncated is True
    assert result.total == 2


def test_self_loop_rejected_by_trigger(graph):
    register_node(graph, node_type="point", node_key="P_SELF", node_name="自环")
    graph.commit()
    with pytest.raises(DatabaseError, match="self-loop"):
        register_edge(
            graph,
            upstream_key="P_SELF",
            downstream_key="P_SELF",
            upstream_type="point",
            downstream_type="point",
        )
        graph.commit()
    graph.rollback()


def test_build_graph_returns_nodes_and_edges(graph):
    exported = build_graph(graph)
    assert len(exported["nodes"]) == 5
    assert len(exported["edges"]) == 4


def test_impact_graph_contains_root_and_dependency_edges(graph):
    result = impact_of(graph, node_type="point", node_key="P_A")
    assert result.root is not None
    assert len(result.edges) == 3
    assert result.root.id in {edge["source"] for edge in result.edges}


def test_200_node_query_is_fast_and_sql_scales_with_depth(db):
    """三层 200 节点：耗时 <1s，SELECT 数只随层数增长。"""
    register_node(db, node_type="point", node_key="PERF_ROOT", node_name="性能根节点")
    first_layer = [f"PERF_DS_{index:02d}" for index in range(20)]
    second_layer = [f"PERF_TASK_{index:03d}" for index in range(100)]
    third_layer = [f"PERF_RPT_{index:03d}" for index in range(80)]
    register_nodes(
        db,
        [
            {"node_type": "dataset", "node_key": key, "node_name": key}
            for key in first_layer
        ]
        + [
            {"node_type": "task", "node_key": key, "node_name": key}
            for key in second_layer
        ]
        + [
            {"node_type": "report", "node_key": key, "node_name": key}
            for key in third_layer
        ],
    )
    register_edges(
        db,
        [
            {
                "upstream_key": "PERF_ROOT",
                "downstream_key": key,
                "upstream_type": "point",
                "downstream_type": "dataset",
            }
            for key in first_layer
        ]
        + [
            {
                "upstream_key": first_layer[index % len(first_layer)],
                "downstream_key": key,
                "upstream_type": "dataset",
                "downstream_type": "task",
            }
            for index, key in enumerate(second_layer)
        ]
        + [
            {
                "upstream_key": second_layer[index % len(second_layer)],
                "downstream_key": key,
                "upstream_type": "task",
                "downstream_type": "report",
            }
            for index, key in enumerate(third_layer)
        ],
    )
    db.commit()

    select_count = 0

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        started = time.perf_counter()
        result = impact_of(
            db,
            node_type="point",
            node_key="PERF_ROOT",
            direction="downstream",
            max_depth=3,
        )
        elapsed = time.perf_counter() - started
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert result.total == 200
    assert elapsed < 1.0, f"影响面查询耗时 {elapsed:.3f}s，超过 1 秒"
    assert select_count <= 7, f"三层 BFS 执行了 {select_count} 条 SELECT，疑似 N+1"
