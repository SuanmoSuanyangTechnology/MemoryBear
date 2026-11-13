"""Request models for the memory-configuration write endpoints.

They mirror the monolith's request models field for field. Write endpoints whose
legacy handlers parsed the raw JSON body themselves are validated explicitly in
the handler so the old ``{success:false, error_code:"VALIDATION_FAILED"}`` 422
body is preserved; declarative endpoints keep FastAPI's standard 422 envelope.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


def _normalize_chunker_strategy(value: str) -> str:
    aliases = {
        "auto": "RecursiveChunker",
        "by_sentence": "SentenceChunker",
        "fixed_tokens": "TokenChunker",
        "递归分块": "RecursiveChunker",
        "token 分块": "TokenChunker",
        "token分块": "TokenChunker",
        "句子分块": "SentenceChunker",
    }
    return aliases.get(value.strip().lower(), value) if isinstance(value, str) else value


ChunkerStrategy = Literal["RecursiveChunker", "TokenChunker", "SentenceChunker"]


class PersonalityAnchorUpdate(BaseModel):
    config_id: UUID
    personality_anchor_enabled: StrictBool
    personality_anchor_auto_solidify: StrictBool
    personality_anchor_read_metadata: StrictBool
    personality_anchor_read_preferences: StrictBool
    personality_anchor_sync_cycle: Literal["daily", "weekly", "manual"]
    personality_anchor_base_principles: str = Field(..., strict=True, max_length=4000)


class ConfigCreateBody(BaseModel):
    """Create payload for both faces.

    Only ``config_name`` is required; ``config_desc`` is optional. Every other
    field a client sends is ignored — the workspace comes from the caller, the
    model slots resolve from the workspace, and ``scene_id``/``pruning_scene``
    are no longer accepted at creation time.
    """

    model_config = ConfigDict(extra="ignore")

    config_name: str
    config_desc: str | None = ""

    @model_validator(mode="after")
    def validate_name(self) -> ConfigCreateBody:
        if not self.config_name or not self.config_name.strip():
            raise ValueError("config_name is required and cannot be empty")
        self.config_name = self.config_name.strip()
        return self


class ConfigUpdateBody(BaseModel):
    config_id: UUID | str
    config_name: str | None = None
    config_desc: str | None = None
    # Compatibility notice does not retire update_config.scene_id. Accept it as
    # a no-op so existing callers are not broken by the ontology retirement.
    # 在v0.5.11实现这些契约变更和字段收缩。
    scene_id: UUID | None = None


class ExtractedUpdateBody(BaseModel):
    """Extracted-config patch; defaults mirror the monolith's ``ConfigUpdateExtracted``.

    The update writes ``model_dump(exclude_unset=True)``, so a field that is not
    sent is left untouched — the defaults only decide whether an omitted field
    is accepted, exactly as in the API.
    """

    config_id: UUID | str
    enable_llm_dedup_blockwise: bool | None = None
    enable_llm_disambiguation: bool | None = None
    deep_retrieval: bool | None = None
    t_type_strict: float | None = Field(None, ge=0.0, le=1.0)
    t_name_strict: float | None = Field(None, ge=0.0, le=1.0)
    t_overall: float | None = Field(None, ge=0.0, le=1.0)
    state: bool | None = None
    chunker_strategy: ChunkerStrategy | None = None
    statement_granularity: int | None = Field(2, ge=1, le=3)
    include_dialogue_context: bool | None = None
    max_context: int | None = Field(1000, gt=100)
    pruning_enabled: bool | None = None
    # update_config_extracted was not announced for removal. Keep its old field
    # in the request contract while treating it as a no-op in the mutation layer.
    # 在v0.5.11实现这些契约变更和字段收缩。
    pruning_scene: str | None = None
    pruning_threshold: float | None = Field(None, ge=0.0, le=0.9)
    enable_self_reflexion: bool | None = None
    iteration_period: Literal["1", "3", "6", "12", "24"] | None = "3"
    reflexion_range: Literal["partial", "all"] | None = "partial"
    baseline: Literal["TIME", "FACT", "TIME-FACT"] | None = "TIME"

    @model_validator(mode="before")
    @classmethod
    def map_chunker_aliases(cls, values: object) -> object:
        if isinstance(values, dict) and "chunker_strategy" in values:
            values = dict(values)
            values["chunker_strategy"] = _normalize_chunker_strategy(values["chunker_strategy"])
        return values


class ForgettingUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: UUID | str
    decay_constant: float | None = Field(None, ge=0.0, le=1.0)
    lambda_time: float | None = Field(None, ge=0.0, le=1.0)
    lambda_mem: float | None = Field(None, ge=0.0, le=1.0)
    offset: float | None = Field(None, ge=0.0, le=1.0)
    max_history_length: int | None = Field(None, ge=10, le=1000)
    forgetting_threshold: float | None = Field(None, ge=0.0, le=1.0)
    min_days_since_access: int | None = Field(None, ge=1, le=365)
    enable_llm_summary: bool | None = None
    max_merge_batch_size: int | None = Field(None, ge=1, le=1000)
    forgetting_interval_hours: int | None = Field(None, ge=1, le=168)


class EmotionUpdateBody(BaseModel):
    config_id: UUID | str
    emotion_enabled: bool


class ReflectionUpdateBody(BaseModel):
    config_id: UUID | str | None = None
    reflection_enabled: bool
    reflection_period_in_hours: str
    reflexion_range: str | None = "partial"
    baseline: str | None = "TIME"
    memory_verify: bool
    quality_assessment: bool


class SceneUpdateBody(BaseModel):
    """Scene config update; defaults mirror the monolith's ``SceneConfig``.

    The update is a full replace (``model_dump(exclude={"config_id"})``), so an
    omitted field is written with its default — same as the API's old handler.
    """

    config_id: UUID
    scene_threshold: float = Field(0.8, ge=0.5, le=0.95)
    scene_history_window_size: int = Field(4, ge=1, le=4)
    scene_min_turns: int = Field(2, ge=0, le=10)
    scene_max_turns: int = Field(10, ge=5, le=100)
    scene_idle_timeout_seconds: int = Field(86400, ge=60, le=30 * 24 * 60 * 60)
    scene_min_chars_to_summary: int = Field(0, ge=0, le=200)
    time_decay_penalty: float = Field(0.1, ge=0, le=0.2)

    @model_validator(mode="after")
    def validate_turn_range(self) -> SceneUpdateBody:
        if self.scene_min_turns >= self.scene_max_turns:
            raise ValueError("scene_min_turns must be less than scene_max_turns")
        return self


class SceneCommunityUpdateBody(BaseModel):
    config_id: UUID
    batch_trigger_count: int = Field(..., ge=1, le=50)
    candidate_community_limit: int = Field(..., ge=1, le=8)
    compare_all_same_category_communities: bool
    rebuild_new_scene_community_count_enabled: bool
    rebuild_new_scene_community_count: int
    rebuild_interval_enabled: bool
    rebuild_interval_days: int


class PreferenceUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: UUID
    preference_engine_enabled: bool | None = None
    custom_keywords: list[str] | None = None

    @model_validator(mode="after")
    def require_update_intent(self) -> PreferenceUpdateBody:
        if self.preference_engine_enabled is None and self.custom_keywords is None:
            raise ValueError("at least one preference config update field is required")
        return self


class PredictionUpdateBody(BaseModel):
    config_id: UUID
    prediction_candidate_limit: int = Field(gt=0)
    prediction_participant_limit: int = Field(gt=0)
    prediction_max_steps: int = Field(ge=1, le=6)
    prediction_recall_limit: int = Field(gt=0)
    prediction_min_valid_memory_count: int = Field(gt=0)
    prediction_embedding_min_similarity: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_limits(self) -> PredictionUpdateBody:
        if self.prediction_participant_limit > self.prediction_candidate_limit:
            raise ValueError("prediction_participant_limit 不能大于 prediction_candidate_limit")
        if self.prediction_min_valid_memory_count > self.prediction_recall_limit:
            raise ValueError("prediction_min_valid_memory_count 不能大于 prediction_recall_limit")
        return self


class ActiveConfigBody(BaseModel):
    config_id: UUID


class SceneSplitDemoBody(BaseModel):
    config_id: UUID | str
    history_messages: list[str] = Field(default_factory=list)
    current_query: str
    scene_threshold: float | None = Field(None, ge=0.5, le=0.95)
    scene_history_window_size: int | None = Field(None, ge=1, le=4)
    scene_min_turns: int | None = Field(None, ge=0, le=10)
    scene_max_turns: int | None = Field(None, ge=5, le=100)

    @model_validator(mode="after")
    def validate_inputs(self) -> SceneSplitDemoBody:
        if not self.current_query.strip():
            raise ValueError("current_query must not be blank")
        self.current_query = self.current_query.strip()
        if (
            self.scene_min_turns is not None
            and self.scene_max_turns is not None
            and self.scene_min_turns >= self.scene_max_turns
        ):
            raise ValueError("scene_min_turns must be less than scene_max_turns")
        return self


class CommunityPreviewBody(BaseModel):
    config_id: UUID
    preview_case: str = Field(min_length=1)


class EmptyBody(BaseModel):
    """Placeholder for endpoints that take no body."""

    model_config = ConfigDict(extra="ignore")
