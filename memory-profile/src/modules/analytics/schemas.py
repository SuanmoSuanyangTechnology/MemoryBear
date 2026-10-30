"""Request and response schemas for internal profile analytics.

The response models describe the payload inside ``ApiResponse.data``. They are
the response half of the external API contract: every ``@external_api`` route
declares one as its ``response_model``, so ``src/external_api_contract.json``
records the payload fields and FastAPI validates each response against them.

The legacy contract omits some keys instead of sending ``null`` — graph edge
items drop unset predicates, community edges drop absent properties, and
``message`` appears only when the service carried a ``message_key``. Those
fields use ``exclude_if`` so re-serialising through the model keeps the payload
byte-identical.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _omitted() -> Any:
    """Optional field that disappears from the payload instead of serialising as null."""

    return Field(default=None, exclude_if=lambda value: value is None)


# ── Request bodies ───────────────────────────────────────────────────────────


class GenerateCacheBody(BaseModel):
    """手动触发缓存生成的请求体（api GenerateCacheRequest 同语义）。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    end_user_id: str | None = Field(
        None,
        description=(
            "终端用户ID（UUID格式）。提供时为该用户生成；不提供时为整个工作空间生成"
        ),
    )


# ── Profile payloads ─────────────────────────────────────────────────────────


class ActionableFinding(BaseModel):
    finding: str
    recommended_action: str


class MemoryInsightData(BaseModel):
    """``memory_insight`` 的响应体（``MemoryInsight`` 去掉内部 ``message_key``）。"""

    memory_insight: str | None
    behavior_pattern: str | None
    key_findings: list[str] | None
    actionable_findings: list[ActionableFinding]
    growth_trajectory: str | None
    updated_at: int | None
    is_cached: bool
    message: str | None = _omitted()


class UserSummaryData(BaseModel):
    """``user_summary`` 的响应体（``UserSummary`` 去掉内部 ``message_key``）。"""

    user_summary: str | None
    personality: str | None
    core_values: str | None
    one_sentence: str | None
    tags: list[str]
    updated_at: int | None
    is_cached: bool
    message: str | None = _omitted()


class MemoryTypeStatistic(BaseModel):
    type: str
    count: int
    percentage: float


class HotMemoryTag(BaseModel):
    name: str
    frequency: int


class InterestItem(BaseModel):
    name: str
    frequency: int


# ── Graph payloads ───────────────────────────────────────────────────────────


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)
    caption: str


class EdgeGroupItem(BaseModel):
    """一条边条目；``to_jsonable`` 的取值可能是字符串/数值，未设置时不出现。"""

    id: str
    type: str
    created_at: Any = _omitted()
    valid_at: Any = _omitted()
    predicate: Any = _omitted()
    predicate_surface: Any = _omitted()
    predicate_description: Any = _omitted()


class UnifiedEdge(BaseModel):
    node_a: str
    node_b: str
    total: int = Field(ge=1)
    edge_type: Literal["SINGLE", "UNIDIRECTIONAL_MULTI", "BIDIRECTIONAL", "MULTI_BIDIRECTIONAL"]
    a_to_b: list[EdgeGroupItem] = Field(default_factory=list)
    b_to_a: list[EdgeGroupItem] = Field(default_factory=list)


class PerTypeStatistic(BaseModel):
    returned: int
    total: int
    limit: int
    truncated: bool


class GraphStatistics(BaseModel):
    total_nodes: int
    total_edges: int
    node_types: dict[str, int]
    edge_types: dict[str, int]
    per_type: dict[str, PerTypeStatistic]


class CommunityStatistics(BaseModel):
    total_nodes: int
    total_edges: int
    node_types: dict[str, int]


class GraphDataResponse(BaseModel):
    """``graph_data`` 的响应体（``GraphData`` 去掉内部 ``message_key``）。"""

    nodes: list[GraphNode]
    edges: list[UnifiedEdge]
    statistics: GraphStatistics
    message: str | None = _omitted()


class CommunityGraphNode(BaseModel):
    """社区图谱节点：与记忆图谱不同，没有 ``caption``。"""

    id: str
    label: str
    properties: dict[str, Any]


class CommunityMembershipEdge(BaseModel):
    """社区成员边：``BELONGS_TO`` 之类的普通边，没有 ``properties``。"""

    id: str
    source: str
    target: str


class CommunityRelationEdge(BaseModel):
    """实体语义关系边：带 ``properties``，``predicate_description`` 可能为 ``null``。"""

    id: str
    source: str
    target: str
    properties: dict[str, Any]
    predicate_description: Any = None


class CommunityGraphResponse(BaseModel):
    """``community_graph`` 的响应体（``CommunityGraph`` 去掉内部 ``message_key``）。"""

    nodes: list[CommunityGraphNode]
    edges: list[CommunityRelationEdge | CommunityMembershipEdge]
    statistics: CommunityStatistics
    message: str | None = _omitted()


# ── Generation payloads ──────────────────────────────────────────────────────


class GenerateCacheUserResult(BaseModel):
    """单用户生成结果（``end_user_id`` 有值时的响应体）。"""

    end_user_id: str
    insight_success: bool | None
    summary_success: bool | None
    errors: list[dict[str, Any]]


class GenerateCacheBatchResult(BaseModel):
    """工作空间批量生成结果（未提供 ``end_user_id`` 时的响应体）。"""

    total_users: int
    successful: int
    failed: int
    errors: list[dict[str, Any]]
