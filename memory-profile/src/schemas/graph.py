"""Graph response value objects."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GraphData(BaseModel):
    """记忆图谱的响应载体；字段名与最终响应 key 一一对应。

    节点/边的装配逻辑（``unified_edges`` / ``properties`` 等）在
    :mod:`src.services.graph`，本类型只承载装配后的结果。
    """

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    statistics: dict[str, Any]
    message_key: str | None = None

    @classmethod
    def empty(cls, message_key: str) -> GraphData:
        return cls(
            nodes=[],
            edges=[],
            statistics={
                "total_nodes": 0,
                "total_edges": 0,
                "node_types": {},
                "edge_types": {},
                "per_type": {},
            },
            message_key=message_key,
        )


class CommunityGraph(BaseModel):
    """社区图谱的响应载体；字段名与最终响应 key 一一对应。"""

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    statistics: dict[str, Any]
    message_key: str | None = None

    @classmethod
    def empty(cls, message_key: str) -> CommunityGraph:
        return cls(
            nodes=[],
            edges=[],
            statistics={
                "total_nodes": 0,
                "total_edges": 0,
                "node_types": {},
                "edge_types": {},
            },
            message_key=message_key,
        )
