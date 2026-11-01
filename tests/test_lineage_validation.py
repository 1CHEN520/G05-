"""Validation-only coverage for the lineage HTTP boundary."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from g05.api.routes.lineage import EdgeRegister, NodeKey, NodeRegister, query_impact
from g05.core.config import settings


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_node_key_rejects_blank_values(value):
    with pytest.raises(ValidationError):
        NodeRegister(node_type="point", node_key=value, node_name="Point A")

    with pytest.raises(ValidationError):
        EdgeRegister(upstream_key=value, downstream_key="DS_1")

    with pytest.raises(ValidationError):
        TypeAdapter(NodeKey).validate_python(value)


def test_registration_payloads_strip_keys_and_names():
    node = NodeRegister(node_type="point", node_key=" P_A ", node_name=" Point A ")
    edge = EdgeRegister(upstream_key=" P_A ", downstream_key=" DS_1 ")

    assert node.node_key == "P_A"
    assert node.node_name == "Point A"
    assert edge.upstream_key == "P_A"
    assert edge.downstream_key == "DS_1"


def test_node_keys_share_the_frozen_96_character_limit():
    too_long = "K" * 97
    with pytest.raises(ValidationError):
        NodeRegister(node_type="point", node_key=too_long, node_name="Point A")
    with pytest.raises(ValidationError):
        EdgeRegister(upstream_key="P_A", downstream_key=too_long)


@pytest.mark.parametrize("value", ["", " ", "\t\n", "K" * 97])
def test_route_rejects_invalid_node_keys_before_querying(value):
    with pytest.raises(HTTPException) as exc_info:
        query_impact(
            db=object(),
            node_type="point",
            node_key=value,
            direction="downstream",
            max_depth=None,
        )
    assert exc_info.value.status_code == 422


def test_route_rejects_depth_above_configured_limit(monkeypatch):
    original_get = settings.get

    def limited_get(key, default=None):
        return 10 if key == "app.lineage.max_depth" else original_get(key, default)

    monkeypatch.setattr(settings, "get", limited_get)
    with pytest.raises(HTTPException) as exc_info:
        query_impact(
            db=object(),
            node_type="point",
            node_key="P_A",
            direction="downstream",
            max_depth=11,
        )
    assert exc_info.value.status_code == 422
