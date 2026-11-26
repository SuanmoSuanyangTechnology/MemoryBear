"""Interface payloads and runtime configuration for the memory-configuration module.

These models are shared by the query service and the HTTP layer. They are pure
Pydantic (no infrastructure imports) and mirror the legacy response contracts of
``api/app/controllers/memory_config_controller.py`` field for field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.schemas.scene_community import BatchTriggerCount, CandidateCommunityLimit
from src.schemas.scene_memory_schema import (
    SceneHistoryWindowSize,
    SceneIdleTimeoutSeconds,
    SceneMaxTurns,
    SceneMinCharsToSummary,
    SceneMinTurns,
    SceneThreshold,
    SceneTimeDecayPenalty,
    validate_scene_turn_range,
)

# ── Simple ontology scenes ────────────────────────────────────────────────────


class SimpleSceneData(BaseModel):
    scene_id: UUID
    scene_name: str


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


# ── Runtime configuration and validation ─────────────────────────────────────

# ==================== Configuration Exception Classes ====================


class ConfigurationError(Exception):
    """Base exception for configuration-related errors.

    This exception includes context information to help with debugging
    and provides detailed error messages for different failure scenarios.
    """

    def __init__(
        self,
        message: str,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
        context: dict[str, Any] | None = None,
    ):
        """Initialize configuration error with context.

        Args:
            message: Error message describing the failure
            config_id: Optional configuration ID for context
            workspace_id: Optional workspace ID for context
            context: Optional additional context information
        """
        self.config_id = config_id
        self.workspace_id = workspace_id
        self.context = context or {}
        self.err_message = message

        # Build detailed error message with context
        detailed_message = message
        if config_id is not None:
            detailed_message = f"Configuration {config_id}: {message}"
        if workspace_id is not None:
            detailed_message = f"{detailed_message} (workspace: {workspace_id})"

        # Add context information if available
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            detailed_message = f"{detailed_message} [Context: {context_str}]"

        super().__init__(detailed_message)


class WorkspaceNotFoundError(ConfigurationError):
    """Raised when workspace does not exist."""

    def __init__(
        self,
        workspace_id: UUID,
        config_id: UUID | None = None,
        message: str | None = None,
    ):
        if message is None:
            message = f"Workspace {workspace_id} not found in database"

        context = {"workspace_id": str(workspace_id)}
        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


class ModelNotFoundError(ConfigurationError):
    """Raised when a required model does not exist."""

    def __init__(
        self,
        model_id: str | UUID,
        model_type: str,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
        message: str | None = None,
    ):
        if message is None:
            message = f"{model_type.title()} model {model_id} not found in database"

        context = {
            "model_id": str(model_id),
            "model_type": model_type,
            "failure_type": "not_found",
        }
        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


class ModelInactiveError(ConfigurationError):
    """Raised when a required model exists but is inactive."""

    def __init__(
        self,
        model_id: str | UUID,
        model_name: str,
        model_type: str,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
        message: str | None = None,
    ):
        if message is None:
            message = f"{model_type.title()} model {model_id} ({model_name}) is inactive"

        context = {
            "model_id": str(model_id),
            "model_name": model_name,
            "model_type": model_type,
            "failure_type": "inactive",
        }
        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


class InvalidConfigError(ConfigurationError):
    """Raised when configuration validation fails."""

    def __init__(
        self,
        message: str,
        field_name: str | None = None,
        invalid_value: Any | None = None,
        config_id: UUID | None = None,
        workspace_id: UUID | None = None,
    ):
        context = {}
        if field_name is not None:
            context["field_name"] = field_name
        if invalid_value is not None:
            context["invalid_value"] = str(invalid_value)
            context["invalid_value_type"] = type(invalid_value).__name__

        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


# ==================== Pydantic Validation Models ====================


class MemoryConfigValidation(BaseModel):
    """Pydantic model for validating memory configuration data from database."""

    config_id: UUID = Field(..., description="Configuration ID (UUID)")
    config_name: str = Field(..., min_length=1, max_length=255)
    workspace_id: UUID = Field(..., description="Workspace UUID")
    workspace_name: str = Field(..., min_length=1, max_length=255)
    tenant_id: UUID = Field(..., description="Tenant UUID")

    embedding_model_id: UUID = Field(..., description="Embedding model UUID (required)")
    embedding_model_name: str = Field(..., min_length=1, max_length=255)
    llm_model_id: UUID = Field(..., description="LLM model UUID (required)")
    llm_model_name: str = Field(..., min_length=1, max_length=255)
    rerank_model_id: UUID | None = Field(None, description="Rerank model UUID (optional)")
    rerank_model_name: str | None = Field(None, max_length=255)

    storage_type: str = Field(..., min_length=1, max_length=50)

    chunker_strategy: str = Field(default="RecursiveChunker", min_length=1, max_length=100)
    reflexion_enabled: bool = Field(default=False)
    reflexion_iteration_period: int = Field(default=3, ge=1, le=100)
    reflexion_range: Literal["partial", "all"] = Field(default="partial")
    reflexion_baseline: Literal["TIME", "FACT", "HYBRID"] = Field(default="TIME")

    llm_params: dict[str, Any] = Field(default_factory=dict)
    embedding_params: dict[str, Any] = Field(default_factory=dict)
    config_version: str = Field(default="2.0", min_length=1, max_length=10)

    @field_validator("config_name", "workspace_name", "embedding_model_name", "llm_model_name")
    @classmethod
    def validate_non_empty_strings(cls, v):
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or whitespace-only")
        return v.strip()

    @field_validator("storage_type")
    @classmethod
    def validate_storage_type(cls, v):
        valid_types = ["neo4j", "elasticsearch", "qdrant", "milvus", "chroma"]
        if v.lower() not in valid_types:
            raise ValueError(f"Storage type must be one of: {valid_types}")
        return v.lower()

    @field_validator("llm_params", "embedding_params")
    @classmethod
    def validate_model_params(cls, v):
        if not isinstance(v, dict):
            raise ValueError("Model parameters must be a dictionary")
        reserved_keys = ["model_id", "model_name", "api_key", "base_url"]
        for key in v.keys():
            if key in reserved_keys:
                raise ValueError(f"Model parameters cannot contain reserved parameter '{key}'")
        return v

    model_config = ConfigDict(validate_assignment=True, extra="forbid")


class WorkspaceValidation(BaseModel):
    """Pydantic model for validating workspace data from database."""

    id: UUID = Field(..., description="Workspace UUID")
    name: str = Field(..., min_length=1, max_length=255)
    tenant_id: UUID = Field(..., description="Tenant UUID")
    storage_type: str | None = Field(None, max_length=50)
    llm: str | None = Field(None)
    embedding: str | None = Field(None)
    rerank: str | None = Field(None)
    is_active: bool = Field(default=True)

    @field_validator("llm", "embedding", "rerank")
    @classmethod
    def validate_model_ids(cls, v):
        if v is None or v == "":
            return None
        try:
            UUID(v.strip())
        except ValueError:
            raise ValueError("Model ID must be a valid UUID string")
        return v.strip()

    @field_validator("is_active")
    @classmethod
    def validate_active_status(cls, v):
        if not v:
            raise ValueError("Workspace must be active for configuration loading")
        return v

    model_config = ConfigDict(validate_assignment=True, extra="forbid")


class ModelValidation(BaseModel):
    """Pydantic model for validating model configuration data."""

    id: UUID = Field(..., description="Model UUID")
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., description="Model type (llm, embedding, rerank)")
    tenant_id: UUID = Field(..., description="Tenant UUID")
    is_active: bool = Field(..., description="Whether model is active")
    is_public: bool = Field(default=False)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        valid_types = ["llm", "embedding", "rerank"]
        if v.lower() not in valid_types:
            raise ValueError(f"Model type must be one of: {valid_types}")
        return v.lower()

    @field_validator("is_active")
    @classmethod
    def validate_active_status(cls, v):
        if not v:
            raise ValueError("Model must be active for configuration use")
        return v

    model_config = ConfigDict(validate_assignment=True, extra="forbid")


# ==================== Validation Helper Functions ====================


def validate_memory_config_data(
    config_data: dict[str, Any], config_id: UUID | None = None
) -> MemoryConfigValidation:
    """Validate memory configuration data using Pydantic model."""
    try:
        return MemoryConfigValidation(**config_data)
    except ValidationError as e:
        error_messages = []
        for error in e.errors():
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            error_messages.append(f"Field '{field_path}': {error['msg']}")

        detailed_message = "Configuration validation failed:\n" + "\n".join(
            f"  - {msg}" for msg in error_messages
        )

        first_error = e.errors()[0] if e.errors() else {}
        first_field = " -> ".join(str(loc) for loc in first_error.get("loc", []))

        raise InvalidConfigError(
            detailed_message,
            field_name=first_field or None,
            invalid_value=first_error.get("input"),
            config_id=config_id,
        )


def validate_workspace_data(
    workspace_data: dict[str, Any], config_id: UUID | None = None
) -> WorkspaceValidation:
    """Validate workspace data using Pydantic model."""
    try:
        return WorkspaceValidation(**workspace_data)
    except ValidationError as e:
        error_messages = []
        for error in e.errors():
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            error_messages.append(f"Field '{field_path}': {error['msg']}")

        detailed_message = "Workspace validation failed:\n" + "\n".join(
            f"  - {msg}" for msg in error_messages
        )

        first_error = e.errors()[0] if e.errors() else {}
        first_field = " -> ".join(str(loc) for loc in first_error.get("loc", []))
        workspace_id = workspace_data.get("id") if isinstance(workspace_data, dict) else None

        raise InvalidConfigError(
            detailed_message,
            field_name=first_field or None,
            invalid_value=first_error.get("input"),
            config_id=config_id,
            workspace_id=workspace_id,
        )


def validate_model_data(
    model_data: dict[str, Any], config_id: UUID | None = None
) -> ModelValidation:
    """Validate model data using Pydantic model."""
    try:
        return ModelValidation(**model_data)
    except ValidationError as e:
        error_messages = []
        for error in e.errors():
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            error_messages.append(f"Field '{field_path}': {error['msg']}")

        detailed_message = "Model validation failed:\n" + "\n".join(
            f"  - {msg}" for msg in error_messages
        )

        first_error = e.errors()[0] if e.errors() else {}
        first_field = " -> ".join(str(loc) for loc in first_error.get("loc", []))

        raise InvalidConfigError(
            detailed_message,
            field_name=first_field or None,
            invalid_value=first_error.get("input"),
            config_id=config_id,
        )


# ==================== Immutable Configuration Data Structure ====================


class MemoryConfig(BaseModel):
    """Immutable memory configuration loaded from database."""

    model_config = ConfigDict(frozen=True)

    config_id: UUID
    config_name: str
    workspace_id: UUID
    workspace_name: str
    tenant_id: UUID

    embedding_model_id: UUID
    embedding_model_name: str
    llm_model_id: UUID
    llm_model_name: str

    storage_type: str

    chunker_strategy: str = "RecursiveChunker"
    reflexion_enabled: bool = False
    reflexion_iteration_period: int = 3
    reflexion_range: str = "partial"
    reflexion_baseline: str = "TIME"

    loaded_at: datetime

    rerank_model_id: UUID | None = None
    rerank_model_name: str | None = None
    video_model_id: UUID | None = None
    video_model_name: str | None = None
    vision_model_id: UUID | None = None
    vision_model_name: str | None = None
    audio_model_id: UUID | None = None
    audio_model_name: str | None = None

    llm_params: dict[str, Any] = Field(default_factory=dict)
    embedding_params: dict[str, Any] = Field(default_factory=dict)
    config_version: str = "2.0"

    # Pipeline config: Deduplication
    enable_llm_dedup_blockwise: bool = False
    enable_llm_disambiguation: bool = False
    deep_retrieval: bool = True
    t_type_strict: float = 0.8
    t_name_strict: float = 0.8
    t_overall: float = 0.8

    # Pipeline config: Statement extraction
    statement_granularity: int = 2
    include_dialogue_context: bool = False
    max_dialogue_context_chars: int = 1000

    # Pipeline config: Forgetting engine
    lambda_time: float = 0.5
    lambda_mem: float = 0.5
    offset: float = 0.0

    # Pipeline config: Pruning
    pruning_enabled: bool = True
    pruning_threshold: float = 0.5

    # Pipeline config: Scene boundary and SceneSummary
    scene_threshold: SceneThreshold = 0.8
    scene_history_window_size: SceneHistoryWindowSize = 4
    scene_min_turns: SceneMinTurns = 2
    scene_max_turns: SceneMaxTurns = 10
    scene_idle_timeout_seconds: SceneIdleTimeoutSeconds = 86400
    scene_min_chars_to_summary: SceneMinCharsToSummary = 0
    time_decay_penalty: SceneTimeDecayPenalty = 0.1

    # Pipeline config: SceneCommunity (rebuild settings are storage-only in v0.4.8)
    batch_trigger_count: BatchTriggerCount = 20
    candidate_community_limit: CandidateCommunityLimit = 3
    compare_all_same_category_communities: bool = False
    rebuild_new_scene_community_count_enabled: bool = True
    rebuild_new_scene_community_count: int = 50
    rebuild_interval_enabled: bool = True
    rebuild_interval_days: int = 1

    # Pipeline config: Emotion extraction
    emotion_enabled: bool = False

    # Prediction engine
    prediction_candidate_limit: int = 8
    prediction_participant_limit: int = 3
    prediction_max_steps: int = 4
    prediction_recall_limit: int = 20
    prediction_min_valid_memory_count: int = 3
    prediction_embedding_min_similarity: float = 0.7
    # Pipeline config: Coding Agent preference extension
    preference_engine_enabled: bool = False
    preference_custom_keywords: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_required_fields(self):
        """Validate configuration after construction."""
        if not self.config_name or not self.config_name.strip():
            raise InvalidConfigError("Configuration name cannot be empty")

        if not self.embedding_model_id:
            raise InvalidConfigError("Embedding model ID is required")

        if not self.llm_model_id:
            raise InvalidConfigError("LLM model ID is required")
        validate_scene_turn_range(self.scene_min_turns, self.scene_max_turns)
        return self

    @classmethod
    def from_validated_data(
        cls, validated_config: MemoryConfigValidation, loaded_at: datetime
    ) -> "MemoryConfig":
        """Create MemoryConfig from validated Pydantic data."""
        return cls(
            config_id=validated_config.config_id,
            config_name=validated_config.config_name,
            workspace_id=validated_config.workspace_id,
            workspace_name=validated_config.workspace_name,
            tenant_id=validated_config.tenant_id,
            embedding_model_id=validated_config.embedding_model_id,
            embedding_model_name=validated_config.embedding_model_name,
            storage_type=validated_config.storage_type,
            chunker_strategy=validated_config.chunker_strategy,
            reflexion_enabled=validated_config.reflexion_enabled,
            reflexion_iteration_period=validated_config.reflexion_iteration_period,
            reflexion_range=validated_config.reflexion_range,
            reflexion_baseline=validated_config.reflexion_baseline,
            loaded_at=loaded_at,
            llm_model_id=validated_config.llm_model_id,
            llm_model_name=validated_config.llm_model_name,
            rerank_model_id=validated_config.rerank_model_id,
            rerank_model_name=validated_config.rerank_model_name,
            llm_params=validated_config.llm_params,
            embedding_params=validated_config.embedding_params,
            config_version=validated_config.config_version,
        )

    def get_model_summary(self) -> dict[str, str | None]:
        """Get a summary of configured models."""
        return {
            "llm": self.llm_model_name,
            "embedding": self.embedding_model_name,
            "rerank": self.rerank_model_name,
        }

    def is_model_configured(self, model_type: str) -> bool:
        """Check if a specific model type is configured."""
        if model_type == "llm":
            return True
        elif model_type == "embedding":
            return True
        elif model_type == "rerank":
            return self.rerank_model_id is not None
        else:
            raise ValueError(f"Unknown model type: {model_type}")
class WorkspaceActiveConfigData(BaseModel):
    """The workspace's active memory configuration id (``workspace-config``)."""

    config_id: str
