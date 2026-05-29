"""Memory API Service request/response schemas.

This module defines Pydantic schemas for the Memory API Service endpoints,
including request validation and response structures for read and write operations.
"""

from typing import Any, Dict, List, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

class TaskStatusResponse(BaseModel):
    """Response schema for task status check.
    
    Attributes:
        status: Task status (PENDING, STARTED, SUCCESS, FAILURE, SKIPPED)
        result: Task result data (available when status is SUCCESS or FAILURE)
    """
    status: str = Field(..., description="Task status")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result when completed")

class CreateEndUserRequest(BaseModel):
    """Request schema for creating an end user.
    
    Attributes:
        other_id: External user identifier (required)
        other_name: Display name for the end user
        memory_config_id: Optional memory config ID. If not provided, uses workspace default.
        app_id: Optional app ID to bind the end user to.
    """
    other_id: str = Field(..., description="External user identifier (required)")
    other_name: Optional[str] = Field("", description="Display name")
    memory_config_id: Optional[str] = Field(None, description="Memory config ID. Falls back to workspace default if not provided.")
    app_id: Optional[str] = Field(None, description="App ID to bind the end user to")

    @field_validator("other_id")
    @classmethod
    def validate_other_id(cls, v: str) -> str:
        """Validate that other_id is not empty."""
        if not v or not v.strip():
            raise ValueError("other_id is required and cannot be empty")
        return v.strip()


class CreateEndUserResponse(BaseModel):
    """Response schema for end user creation.
    
    Attributes:
        id: Created end user UUID
        other_id: External user identifier
        other_name: Display name
        workspace_id: Workspace the user belongs to
        memory_config_id: Connected memory config ID
    """
    id: str = Field(..., description="End user UUID")
    other_id: str = Field(..., description="External user identifier")
    other_name: str = Field("", description="Display name")
    workspace_id: str = Field(..., description="Workspace ID")
    memory_config_id: Optional[str] = Field(None, description="Connected memory config ID")


class MemoryConfigItem(BaseModel):
    """Schema for a single memory config in the list response.
    
    Attributes:
        config_id: Configuration UUID
        config_name: Configuration name
        config_desc: Configuration description
        is_default: Whether this is the workspace default config
        scene_name: Associated ontology scene name
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """
    config_id: str = Field(..., description="Configuration ID")
    config_name: str = Field(..., description="Configuration name")
    config_desc: Optional[str] = Field(None, description="Configuration description")
    is_default: bool = Field(False, description="Whether this is the workspace default")
    scene_name: Optional[str] = Field(None, description="Associated ontology scene name")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")

# ========== V1 记忆配置管理接口 Schema ==========

class ListConfigsResponse(BaseModel):
    """Response schema for listing memory configs.
    
    Attributes:
        configs: List of memory config items
        total: Total number of configs
    """
    configs: List[MemoryConfigItem] = Field(default_factory=list, description="List of configs")
    total: int = Field(0, description="Total number of configs")

class ConfigCreateRequest(BaseModel):
    """Request schema for creating a new memory config."""
    config_name: str = Field(..., description="Configuration name")
    config_desc: Optional[str] = Field("", description="Configuration description")
    scene_id: uuid.UUID = Field(..., description="Associated ontology scene ID (UUID, required)")
    
    llm_id: Optional[str] = Field(None, description="LLM model configuration ID")
    embedding_id: Optional[str] = Field(None, description="Embedding model configuration ID")
    rerank_id: Optional[str] = Field(None, description="Reranking model configuration ID")
    reflection_model_id: Optional[str] = Field(None, description="Reflection model ID")
    emotion_model_id: Optional[str] = Field(None, description="Emotion analysis model ID")

    @field_validator("config_name")
    @classmethod
    def validate_config_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("config_name is required and cannot be empty")
        return v.strip()

class ConfigUpdateRequest(BaseModel):
    """Request schema for updating memory config basic info.
    
    Attributes:
        config_id: Configuration UUID to update (required)
        config_name: New configuration name
        config_desc: New configuration description
        scene_id: New associated ontology scene ID
    """
    config_id: str = Field(..., description="Configuration ID to update")
    config_name: Optional[str] = Field(None, description="Configuration name")
    config_desc: Optional[str] = Field(None, description="Configuration description")
    scene_id: Optional[uuid.UUID] = Field(None, description="Associated ontology scene ID")

    @field_validator("config_id")
    @classmethod
    def validate_config_id(cls, v: str) -> str:
        """Validate that config_id is not empty."""
        if not v or not v.strip():
            raise ValueError("config_id is required and cannot be empty")
        return v.strip()
    
class ConfigUpdateExtractedRequest(BaseModel):
    """Request schema for updating memory config extracted parameters.

    Attributes:
        config_id: Configuration UUID to update (required)
        llm_id: Optional LLM model configuration ID
        audio_id: Optional audio model configuration ID
        vision_id: Optional vision model configuration ID
        video_id: Optional video model configuration ID
        embedding_id: Optional embedding model configuration ID
        rerank_id: Optional reranking model configuration ID
        enable_llm_dedup_blockwise: Optional toggle for LLM decision deduplication
        enable_llm_disambiguation: Optional toggle for LLM decision disambiguation
        deep_retrieval: Optional toggle for deep retrieval

        t_type_strict: Optional float (0-1) for type strictness threshold
        t_name_strict: Optional float (0-1) for name strictness threshold
        t_overall: Optional float (0-1) for overall strictness threshold
        state: Optional boolean for config active state
        chunker_strategy: Optional string for memory chunking strategy
        statement_granularity: Optional int (1-3) for statement extraction granularity
        include_dialogue_context: Optional boolean for including dialogue context in retrieval
        max_context: Optional int for maximum dialogue context length in characters
        pruning_enabled: Optional boolean to enable intelligent semantic pruning
        pruning_scene: Optional string for semantic pruning scene
        pruning_threshold: Optional float (0-0.9) for semantic pruning threshold
        enable_self_reflexion: Optional boolean to enable self-reflexion
        iteration_period: Optional string for reflexion iteration period in hours (1, 3, 6, 12, 24)
        reflexion_range: Optional string for reflexion range (partial or all)
        baseline: Optional string for baseline (TIME/FACT/TIME-FACT)
    
    """
    config_id: str = Field(..., description="Configuration ID (UUID)")
    llm_id: Optional[str] = Field(None, description="LLM model configuration ID")
    audio_id: Optional[str] = Field(None, description="Audio model ID")
    vision_id: Optional[str] = Field(None, description="Vision model ID")
    video_id: Optional[str] = Field(None, description="Video model ID")
    embedding_id: Optional[str] = Field(None, description="Embedding model configuration ID")
    rerank_id: Optional[str] = Field(None, description="Reranking model configuration ID")
    enable_llm_dedup_blockwise: Optional[bool] = Field(None, description="Enable LLM decision deduplication")
    enable_llm_disambiguation: Optional[bool] = Field(None, description="Enable LLM decision disambiguation")
    deep_retrieval: Optional[bool] = Field(None, description="Deep retrieval toggle")
    
    t_type_strict: Optional[float] = Field(None, ge=0.0, le=1.0, description="type strictness threshold")
    t_name_strict: Optional[float] = Field(None, ge=0.0, le=1.0, description="name strictness threshold")
    t_overall: Optional[float] = Field(None, ge=0.0, le=1.0, description="overall strictness threshold")
    state: Optional[bool] = Field(None, description="config active state")
     # 句子提取 
    chunker_strategy: Optional[str] = Field(None, description="memory chunking strategy")
    statement_granularity: Optional[int] = Field(None, ge=1, le=3, description="statement extraction granularity")
    include_dialogue_context: Optional[bool] = Field(None, description="whether to include dialogue context in retrieval")
    max_context: Optional[int] = Field(None, gt=100, description="maximum dialogue context length in characters")
     # 剪枝配置：与 runtime.json 中 pruning 段对应
    pruning_enabled: Optional[bool] = Field(None, description="whether to enable intelligent semantic pruning")
    pruning_scene: Optional[str] = Field(None, description="semantic pruning scene")
    pruning_threshold: Optional[float] = Field(None, ge=0.0, le=0.9, description="semantic pruning threshold (0-0.9)")
    enable_self_reflexion: Optional[bool] = Field(None, description="whether to enable self-reflexion")
    iteration_period: Optional[Literal["1", "3", "6", "12", "24"]] = Field(None, description="reflexion iteration period in hours (1, 3, 6, 12, 24)")
    reflexion_range: Optional[Literal["partial", "all"]] = Field(None, description="reflexion range: partial/all")
    baseline: Optional[Literal["TIME", "FACT", "TIME-FACT"]] = Field(None, description="baseline: TIME/FACT/TIME-FACT")

    @field_validator("config_id")
    @classmethod
    def validate_config_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("config_id is required and cannot be empty")
        return v.strip()

class ConfigUpdateForgettingRequest(BaseModel):
    """Request schema for updating memory config forgetting parameters.

    Attributes:
        config_id: Configuration UUID to update (required)
        decay_constant: Decay constant for forgetting
        lambda_time: Time decay parameter
        lambda_mem: Memory decay parameter
        offset: Offset for forgetting curve
        max_history_length: Maximum history length to consider for forgetting
        forgetting_threshold: Threshold for forgetting
        min_days_since_access: Minimum days since last access to trigger forgetting
        enable_llm_summary: Whether to use LLM-generated summaries for forgetting
        max_merge_batch_size: Maximum batch size for merging nodes during forgetting
        forgetting_interval_hours: Interval in hours for periodic forgetting
    
    """
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    config_id: str = Field(..., description="Configuration ID (UUID)")
    decay_constant: Optional[float] = Field(None, ge=0.0, le=1.0, description="Decay constant for forgetting")
    lambda_time: Optional[float] = Field(None, ge=0.0, le=1.0, description="Time decay parameter")
    lambda_mem: Optional[float] = Field(None, ge=0.0, le=1.0, description="Memory decay parameter")
    offset: Optional[float] = Field(None, ge=0.0, le=1.0, description="Offset for forgetting curve")
    max_history_length: Optional[int] = Field(None, ge=10, le=1000, description="Maximum history length to consider for forgetting")
    forgetting_threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="Forgetting threshold")
    min_days_since_access: Optional[int] = Field(None, ge=1, le=365, description="Minimum days since last access to trigger forgetting")
    enable_llm_summary: Optional[bool] = Field(None, description="Whether to use LLM-generated summaries for forgetting")
    max_merge_batch_size: Optional[int] = Field(None, ge=1, le=1000, description="Maximum batch size for merging nodes during forgetting")
    forgetting_interval_hours: Optional[int] = Field(None, ge=1, le=168, description="Interval in hours for periodic forgetting")

    @field_validator("config_id")
    @classmethod
    def validate_config_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("config_id is required and cannot be empty")
        return v.strip()

class EmotionConfigUpdateRequest(BaseModel):
    """Request schema for updating memory config emotion parameters.

    Attributes:
        config_id: Configuration UUID to update (required)
        emotion_enabled: Whether to enable emotion extraction
        emotion_model_id: Emotion analysis model ID
        emotion_extract_keywords: Whether to extract emotion keywords
        emotion_min_intensity: Minimum emotion intensity threshold (0.0-1.0)
        emotion_enable_subject: Whether to enable subject classification for emotions
    """
    config_id: str = Field(..., description="Configuration ID (UUID)")
    emotion_enabled: bool = Field(..., description="Whether to enable emotion extraction")
    emotion_model_id: Optional[str] = Field(None, description="Emotion analysis model ID")
    emotion_extract_keywords: bool = Field(..., description="Whether to extract emotion keywords")
    emotion_min_intensity: float = Field(..., ge=0.0, le=1.0, description="Minimum emotion intensity threshold")
    emotion_enable_subject: bool = Field(..., description="Whether to enable subject classification for emotions")

    @field_validator("config_id")
    @classmethod
    def validate_config_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("config_id is required and cannot be empty")
        return v.strip()

class ReflectionConfigUpdateRequest(BaseModel):
    """Request schema for updating memory config reflection parameters.

    Attributes:
        config_id: Configuration UUID to update (required)
        reflection_enabled: Whether to enable self-reflection
        reflection_period_in_hours: Reflection iteration period in hours
        reflexion_range: Reflection range (partial or all)
        baseline: Baseline for reflection (TIME/FACT/TIME-FACT)
        reflection_model_id: Reflection model ID
        memory_verify: Whether to enable memory verification
        quality_assessment: Whether to enable quality assessment
    """
    config_id: str = Field(..., description="Configuration ID (UUID)")
    reflection_enabled: bool = Field(..., description="Whether to enable self-reflection")
    reflection_period_in_hours: str = Field(..., description="Reflection iteration period in hours")
    reflexion_range: Literal["partial", "all"] = Field(..., description="Reflection range: partial/all")
    baseline: Literal["TIME", "FACT", "TIME-FACT"] = Field(..., description="Baseline: TIME/FACT/TIME-FACT")
    reflection_model_id: str = Field(..., description="Reflection model ID")
    memory_verify: bool = Field(..., description="Whether to enable memory verification")
    quality_assessment: bool = Field(..., description="Whether to enable quality assessment")

    @field_validator("config_id")
    @classmethod
    def validate_config_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("config_id is required and cannot be empty")
        return v.strip()
