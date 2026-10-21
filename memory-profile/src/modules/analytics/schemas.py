"""Request and graph response schemas for internal profile analytics."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)
    caption: str


class EdgeGroupItem(BaseModel):
    id: str
    type: str
    created_at: str | None = None
    valid_at: str | None = None
    predicate: str | None = None
    predicate_surface: str | None = None
    predicate_description: str | None = None


class UnifiedEdge(BaseModel):
    node_a: str
    node_b: str
    total: int = Field(ge=1)
    edge_type: Literal["SINGLE", "UNIDIRECTIONAL_MULTI", "BIDIRECTIONAL", "MULTI_BIDIRECTIONAL"]
    a_to_b: list[EdgeGroupItem] = Field(default_factory=list)
    b_to_a: list[EdgeGroupItem] = Field(default_factory=list)
