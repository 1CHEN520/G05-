"""血缘查询与注册服务。"""

from g05.lineage.impact import (
    ImpactNode,
    ImpactResult,
    build_graph,
    impact_of,
    register_edge,
    register_edges,
    register_node,
    register_nodes,
    resolve_node,
)

__all__ = [
    "ImpactNode",
    "ImpactResult",
    "build_graph",
    "impact_of",
    "register_edge",
    "register_edges",
    "register_node",
    "register_nodes",
    "resolve_node",
]
