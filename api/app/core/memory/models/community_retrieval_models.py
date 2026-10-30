"""Internal models for community retrieval evidence and diagnostics."""

from typing import Any, Literal

from pydantic import BaseModel, Field


EmptyCommunityReason = Literal[
    "no_community_hit", "no_scene_hit_in_communities", "no_valid_evidence"
]


class CommunityEvidence(BaseModel):
    node_id: str
    node_type: Literal["SceneSummary"] = "SceneSummary"
    text: str


class CommunityRetrievalTrace(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    model_config_id: str = ""
    model_name: str = ""
    vector_dimension: int = 0
    embedding_calls: int = 0
    semantic_degraded: bool = False
    database_requests: int = Field(
        default=0, description="Logical storage recall calls; providers may issue multiple physical database requests.",
    )
    backend: str | None = None
    matched_community_count: int | None = Field(
        default=None, description="Exact match total, unavailable from shared bounded candidate recall.",
    )
    recalled_community_count: int = Field(
        default=0, description="Community candidates returned by storage before local score validation.",
    )
    selected_communities: list[dict[str, Any]] = Field(default_factory=list)
    members: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-community recalled_scene_count, optional exact matched_scene_count and selected candidates.",
    )
    merged_ranking: list[dict[str, Any]] = Field(default_factory=list)
    packing_actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    final_tokens: int = 0
    empty_reason: EmptyCommunityReason | None = None
    ignored_compatibility_parameters: dict[str, Any] = Field(default_factory=dict)
    stage_duration_ms: dict[str, float] = Field(default_factory=dict)
    status: Literal["running", "completed", "failed"] = "running"
    error_reason: str | None = None
