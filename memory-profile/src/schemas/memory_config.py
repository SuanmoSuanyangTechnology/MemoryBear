"""Domain payloads for the memory-configuration module.

These models are shared by the query service and the HTTP layer. They are pure
Pydantic (no infrastructure imports) and mirror the legacy response contracts of
``api/app/controllers/memory_config_controller.py`` field for field.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── Personality anchor ───────────────────────────────────────────────────────


class PersonalityAnchorData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    config_id: UUID
    is_default: bool
    personality_anchor_enabled: bool
    personality_anchor_auto_solidify: bool
    personality_anchor_read_metadata: bool
    personality_anchor_read_preferences: bool
    personality_anchor_sync_cycle: Literal["daily", "weekly", "manual"]
    personality_anchor_base_principles: str


# ── Extraction engine ────────────────────────────────────────────────────────


class ExtractedConfigData(BaseModel):
    llm_id: str | None
    embedding_id: str | None
    rerank_id: str | None
    vision_id: str | None
    audio_id: str | None
    video_id: str | None
    enable_llm_dedup_blockwise: bool | None
    enable_llm_disambiguation: bool | None
    deep_retrieval: bool | None
    t_type_strict: float | None
    t_name_strict: float | None
    t_overall: float | None
    chunker_strategy: str | None
    statement_granularity: int | None
    include_dialogue_context: bool | None
    max_context: int | None
    pruning_enabled: bool | None
    # read_config_extracted is not in the announced removal list. The field is
    # retained for response compatibility without reading the retired ontology.
    # 在v0.5.11实现这些契约变更和字段收缩。
    pruning_scene: str | None
    pruning_threshold: float | None
    enable_self_reflexion: bool | None
    iteration_period: str | None
    reflexion_range: str | None
    baseline: str | None
    is_default: bool


# ── Forgetting engine ────────────────────────────────────────────────────────


class ForgettingConfigData(BaseModel):
    config_id: UUID
    decay_constant: float
    lambda_time: float
    lambda_mem: float
    forgetting_rate: float
    offset: float
    max_history_length: int
    forgetting_threshold: float
    min_days_since_access: int
    enable_llm_summary: bool
    max_merge_batch_size: int
    forgetting_interval_hours: int
    is_default: bool


# ── Emotion engine ───────────────────────────────────────────────────────────


class EmotionConfigData(BaseModel):
    config_id: UUID
    emotion_enabled: bool | None
    emotion_model_id: str | None
    is_default: bool


# ── Reflection engine ────────────────────────────────────────────────────────


class ReflectionConfigData(BaseModel):
    config_id: UUID
    reflection_enabled: bool | None
    reflection_period_in_hours: str | None
    reflexion_range: str | None
    baseline: str | None
    reflection_model_id: str | None
    memory_verify: bool | None
    quality_assessment: bool | None
    is_default: bool


# ── Scene boundary ───────────────────────────────────────────────────────────

SceneThreshold = Annotated[float, Field(ge=0.5, le=0.95)]
SceneHistoryWindowSize = Annotated[int, Field(ge=1, le=4)]
SceneMinTurns = Annotated[int, Field(ge=0, le=10)]
SceneMaxTurns = Annotated[int, Field(ge=5, le=100)]
SceneIdleTimeoutSeconds = Annotated[int, Field(ge=60, le=30 * 24 * 60 * 60)]
SceneMinCharsToSummary = Annotated[int, Field(ge=0, le=200)]
SceneTimeDecayPenalty = Annotated[float, Field(ge=0, le=0.2)]


class SceneConfigData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    config_id: UUID
    scene_threshold: SceneThreshold
    scene_history_window_size: SceneHistoryWindowSize
    scene_min_turns: SceneMinTurns
    scene_max_turns: SceneMaxTurns
    scene_idle_timeout_seconds: SceneIdleTimeoutSeconds
    scene_min_chars_to_summary: SceneMinCharsToSummary
    time_decay_penalty: SceneTimeDecayPenalty

    @model_validator(mode="after")
    def validate_scene_ranges(self) -> SceneConfigData:
        if self.scene_min_turns >= self.scene_max_turns:
            raise ValueError("scene_min_turns must be less than scene_max_turns")
        return self


# ── Scene community ──────────────────────────────────────────────────────────

BatchTriggerCount = Annotated[int, Field(ge=1, le=50)]
CandidateCommunityLimit = Annotated[int, Field(ge=1, le=8)]


class SceneCommunityConfigData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    config_id: UUID
    batch_trigger_count: BatchTriggerCount
    candidate_community_limit: CandidateCommunityLimit
    compare_all_same_category_communities: bool
    rebuild_new_scene_community_count_enabled: bool
    rebuild_new_scene_community_count: int
    rebuild_interval_enabled: bool
    rebuild_interval_days: int


# ── Prediction engine ────────────────────────────────────────────────────────


class PredictionConfigData(BaseModel):
    config_id: UUID
    is_default: bool
    prediction_candidate_limit: int
    prediction_participant_limit: int
    prediction_max_steps: int
    prediction_recall_limit: int
    prediction_min_valid_memory_count: int
    prediction_embedding_min_similarity: float


# ── Preference engine ────────────────────────────────────────────────────────


class PreferenceConfigData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: UUID
    preference_engine_enabled: bool
    default_keywords: list[str]
    custom_keywords: list[str]
    effective_keywords: list[str]


class PreferenceConfigUpdateData(BaseModel):
    """Update payload for the preference endpoint (P2 write)."""

    model_config = ConfigDict(extra="forbid")

    config_id: UUID
    preference_engine_enabled: bool | None = None
    custom_keywords: list[str] | None = None


# ── Mutation results ─────────────────────────────────────────────────────────


class ConfigCreateResult(BaseModel):
    affected: int
    config_id: str


class ConfigUpdateResult(BaseModel):
    affected: int


class ReflectionMutationData(BaseModel):
    config_id: UUID
    enable_self_reflexion: bool
    iteration_period: str
    reflexion_range: str | None
    baseline: str | None
    memory_verify: bool
    quality_assessment: bool


class ConfigSummaryData(BaseModel):
    config_id: str
    config_name: str
    config_desc: str | None
    workspace_id: str | None
    end_user_id: str | None
    config_id_old: int | None
    apply_id: str | None
    # read_all_config is not in the announced removal list. Preserve its legacy
    # response keys while the service stops querying/writing retired ontology data.
    # 在v0.5.11实现这些契约变更和字段收缩。
    scene_id: str | None
    scene_name: str | None
    is_system_default: bool | None
    llm_id: str | None
    embedding_id: str | None
    rerank_id: str | None
    enable_llm_dedup_blockwise: bool | None
    enable_llm_disambiguation: bool | None
    deep_retrieval: bool | None
    t_type_strict: float | None
    t_name_strict: float | None
    t_overall: float | None
    state: bool | None
    chunker_strategy: str | None
    pruning_enabled: bool | None
    pruning_scene: str | None
    pruning_threshold: float | None
    enable_self_reflexion: bool | None
    iteration_period: str | None
    reflexion_range: str | None
    baseline: str | None
    statement_granularity: int | None
    include_dialogue_context: bool | None
    max_context: int | None
    lambda_time: float | None
    lambda_mem: float | None
    offset: float | None
    created_at: int | None
    updated_at: int | None
    is_active: bool


class DeleteConfigResult(BaseModel):
    config_id: str
    is_default: bool


class ConfigWarning(BaseModel):
    model_type: str | None = None
    model_id: str | None = None
    source: str | None = None
    message: str


class ValidateConfigResult(BaseModel):
    valid: bool
    config_id: str | None = None
    config_name: str | None = None
    warnings: list[ConfigWarning] | None = None


class ActiveConfigResult(BaseModel):
    config_id: UUID
    warnings: list[ConfigWarning]
    success: bool
