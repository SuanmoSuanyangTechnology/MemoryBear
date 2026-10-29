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
    database_requests: int = 0
    matched_community_count: int = Field(
        default=0, description="Communities matching filters and the active query before top-K selection.",
    )
    selected_communities: list[dict[str, Any]] = Field(default_factory=list)
    members: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-community matched_scene_count before top-K selection and selected candidates.",
    )
    merged_ranking: list[dict[str, Any]] = Field(default_factory=list)
    skipped_hits: list[dict[str, Any]] = Field(
        default_factory=list, description="Hits skipped for invalid scores, including stage and raw score diagnostics.",
    )
    packing_actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    final_tokens: int = 0
    empty_reason: EmptyCommunityReason | None = None
    ignored_compatibility_parameters: dict[str, Any] = Field(default_factory=dict)
    stage_duration_ms: dict[str, float] = Field(default_factory=dict)
    status: Literal["running", "completed", "failed"] = "running"
    error_reason: str | None = None
