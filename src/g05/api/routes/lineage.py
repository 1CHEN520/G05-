"""血缘 API：影响面查询、图导出、节点与边注册。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session

from g05.api.deps import get_db
from g05.core.config import settings
from g05.lineage.impact import build_graph, impact_of, register_edge, register_node

router = APIRouter(prefix="/lineage", tags=["血缘"])

NodeKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=96),
]
NodeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class NodeRegister(BaseModel):
    node_type: str = Field(..., pattern="^(point|dataset|report|task|api)$")
    node_key: NodeKey
    node_name: NodeName
    owner: str | None = None


class EdgeRegister(BaseModel):
    upstream_type: str = Field("point", pattern="^(point|dataset|report|task|api)$")
    upstream_key: NodeKey
    downstream_type: str = Field("report", pattern="^(point|dataset|report|task|api)$")
    downstream_key: NodeKey
    field_name: str | None = None
    relation: str = Field("read", pattern="^(read|join|aggregate)$")


@router.get("/impact", summary="影响面查询（测点改名影响谁）")
def query_impact(
    db: Session = Depends(get_db),
    node_type: str = Query("point", pattern="^(point|dataset|report|task|api)$"),
    node_key: str = Query(..., description="血缘节点业务键"),
    direction: str = Query("downstream", pattern="^(downstream|upstream)$"),
    max_depth: int | None = Query(None, ge=0, le=20),
):
    node_key = node_key.strip()
    if not node_key or len(node_key) > 96:
        raise HTTPException(422, "node_key must contain 1 to 96 non-whitespace characters")
    configured_depth_limit = settings.get("app.lineage.max_depth", 10)
    if max_depth is not None and max_depth > configured_depth_limit:
        raise HTTPException(
            422,
            f"max_depth cannot exceed app.lineage.max_depth ({configured_depth_limit})",
        )
    result = impact_of(
        db,
        node_type=node_type,
        node_key=node_key,
        direction=direction,
        max_depth=max_depth,
    )
    if result.root is None:
        raise HTTPException(404, f"血缘节点不存在: {node_type}:{node_key}")
    return result.to_dict()


@router.get("/graph", summary="导出血缘图（节点+边）")
def export_graph(
    db: Session = Depends(get_db),
    limit: int = Query(500, ge=1, le=5000),
):
    return build_graph(db, limit=limit)


@router.post("/nodes", status_code=201, summary="注册血缘节点")
def create_node(payload: NodeRegister, db: Session = Depends(get_db)):
    node = register_node(
        db,
        node_type=payload.node_type,
        node_key=payload.node_key,
        node_name=payload.node_name,
        owner=payload.owner,
    )
    db.commit()
    return {"id": node.id, "node_type": node.node_type, "node_key": node.node_key}


@router.post("/edges", status_code=201, summary="注册依赖边")
def create_edge(payload: EdgeRegister, db: Session = Depends(get_db)):
    try:
        edge = register_edge(
            db,
            upstream_key=payload.upstream_key,
            downstream_key=payload.downstream_key,
            upstream_type=payload.upstream_type,
            downstream_type=payload.downstream_type,
            field_name=payload.field_name,
            relation=payload.relation,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {
        "id": edge.id,
        "upstream_id": edge.upstream_id,
        "downstream_id": edge.downstream_id,
    }
