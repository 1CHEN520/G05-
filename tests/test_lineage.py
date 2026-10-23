"""血缘影响面测试：方向、深度、环检测、性能。"""

from __future__ import annotations

import time

import pytest

from g05.lineage.impact import (
    build_graph,
    impact_of,
    register_edge,
    register_node,
)


@pytest.fixture()
def graph(db):
    """构造：point A -> dataset D -> report R / task T；point B -> R"""
    register_node(db, node_type="point", node_key="P_A", node_name="测点A", owner="设备部")
    register_node(db, node_type="point", node_key="P_B", node_name="测点B", owner="设备部")
    register_node(db, node_type="dataset", node_key="DS_1", node_name="清洗宽表", owner="数据组")
    register_node(db, node_type="report", node_key="RPT_1", node_name="OEE日报", owner="生产部")
    register_node(db, node_type="task", node_key="TASK_1", node_name="特征计算", owner="算法组")

    register_edge(db, upstream_key="P_A", downstream_key="DS_1", upstream_type="point", downstream_type="dataset")
    register_edge(db, upstream_key="P_B", downstream_key="DS_1", upstream_type="point", downstream_type="dataset")
    register_edge(db, upstream_key="DS_1", downstream_key="RPT_1", upstream_type="dataset", downstream_type="report")
    register_edge(db, upstream_key="DS_1", downstream_key="TASK_1", upstream_type="dataset", downstream_type="task")
    db.commit()
    return db


def test_downstream_transitive(graph):
    result = impact_of(graph, node_type="point", node_key="P_A", direction="downstream")
    keys = {n.node_key for n in result.nodes}
    assert keys == {"DS_1", "RPT_1", "TASK_1"}
    assert result.total == 3


def test_downstream_depth_levels(graph):
    result = impact_of(graph, node_type="point", node_key="P_A", direction="downstream")
    depth = {n.node_key: n.depth for n in result.nodes}
    assert depth["DS_1"] == 1
    assert depth["RPT_1"] == 2


def test_upstream_direction(graph):
    result = impact_of(graph, node_type="report", node_key="RPT_1", direction="upstream")
    keys = {n.node_key for n in result.nodes}
    assert keys == {"DS_1", "P_A", "P_B"}


def test_max_depth_limits_traversal(graph):
    result = impact_of(graph, node_type="point", node_key="P_A", direction="downstream", max_depth=1)
    keys = {n.node_key for n in result.nodes}
    assert keys == {"DS_1"}


def test_path_recorded(graph):
    result = impact_of(graph, node_type="point", node_key="P_A", direction="downstream")
    rpt = next(n for n in result.nodes if n.node_key == "RPT_1")
    assert rpt.path == ["P_A", "DS_1", "RPT_1"]


def test_missing_node_returns_empty(graph):
    result = impact_of(graph, node_type="point", node_key="NOT_EXIST")
    assert result.root is None
    assert result.nodes == []


def test_summary_groups_by_type(graph):
    result = impact_of(graph, node_type="point", node_key="P_A", direction="downstream")
    summary = {k: len(v) for k, v in result.by_type().items()}
    assert summary == {"dataset": 1, "report": 1, "task": 1}


def test_self_loop_rejected_by_trigger(graph):
    """数据库触发器应拦截自环边。"""
    from sqlalchemy.exc import IntegrityError

    node = register_node(graph, node_type="point", node_key="P_SELF", node_name="自环")
    graph.commit()
    with pytest.raises(Exception):
        register_edge(
            graph, upstream_key="P_SELF", downstream_key="P_SELF", upstream_type="point", downstream_type="point"
        )
        graph.commit()
    graph.rollback()


def test_build_graph_returns_nodes_and_edges(graph):
    g = build_graph(graph)
    assert len(g["nodes"]) == 5
    assert len(g["edges"]) == 4


def test_impact_query_performance(graph):
    """硬指标：影响面查询 <1 秒。"""
    started = time.perf_counter()
    impact_of(graph, node_type="point", node_key="P_A", direction="downstream")
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"影响面查询耗时 {elapsed:.3f}s，超过 1 秒"
