# -*- coding: utf-8 -*-
"""Memory Configuration Schemas

This module provides schema definitions for memory configuration.

Classes:
    MemoryConfig: Immutable memory configuration loaded from database
    MemoryConfigValidation: Pydantic model for configuration validation
    WorkspaceValidation: Pydantic model for workspace validation
    ModelValidation: Pydantic model for model configuration validation
    ConfigurationError: Base exception for configuration-related errors
    WorkspaceNotFoundError: Raised when workspace does not exist
    ModelNotFoundError: Raised when a required model does not exist
    ModelInactiveError: Raised when a required model exists but is inactive
    InvalidConfigError: Raised when configuration validation fails
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# ==================== Configuration Exception Classes ====================


class ConfigurationError(Exception):
    """Base exception for configuration-related errors.
    
    This exception includes context information to help with debugging
    and provides detailed error messages for different failure scenarios.
    """
    
    def __init__(
        self,
        message: str,
        config_id: Optional[int] = None,
        workspace_id: Optional[UUID] = None,
        context: Optional[Dict[str, Any]] = None,
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
        config_id: Optional[int] = None,
        message: Optional[str] = None,
    ):
        if message is None:
            message = f"Workspace {workspace_id} not found in database"
        
        context = {"workspace_id": str(workspace_id)}
        super().__init__(message, config_id=config_id, workspace_id=workspace_id, context=context)


class ModelNotFoundError(ConfigurationError):
    """Raised when a required model does not exist."""
    
    def __init__(
        self,
        model_id: Union[str, UUID],
        model_type: str,
        config_id: Optional[int] = None,
        workspace_id: Optional[UUID] = None,
        message: Optional[str] = None,
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
        model_id: Union[str, UUID],
        model_name: str,
        model_type: str,
        config_id: Optional[int] = None,
        workspace_id: Optional[UUID] = None,
        message: Optional[str] = None,
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
        field_name: Optional[str] = None,
        invalid_value: Optional[Any] = None,
        config_id: Optional[int] = None,
        workspace_id: Optional[UUID] = None,
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
    
    config_id: int = Field(..., gt=0, description="Configuration ID must be positive")
    config_name: str = Field(..., min_length=1, max_length=255)
    workspace_id: UUID = Field(..., description="Workspace UUID")
    workspace_name: str = Field(..., min_length=1, max_length=255)
    tenant_id: UUID = Field(..., description="Tenant UUID")
    
    embedding_model_id: UUID = Field(..., description="Embedding model UUID (required)")
    embedding_model_name: str = Field(..., min_length=1, max_length=255)
    llm_model_id: UUID = Field(..., description="LLM model UUID (required)")
    llm_model_name: str = Field(..., min_length=1, max_length=255)
    rerank_model_id: Optional[UUID] = Field(None, description="Rerank model UUID (optional)")
    rerank_model_name: Optional[str] = Field(None, max_length=255)
    
    storage_type: str = Field(..., min_length=1, max_length=50)
    
    chunker_strategy: str = Field(default="RecursiveChunker", min_length=1, max_length=100)
    reflexion_enabled: bool = Field(default=False)
    reflexion_iteration_period: int = Field(default=3, ge=1, le=100)
    reflexion_range: Literal["retrieval", "all"] = Field(default="retrieval")
    reflexion_baseline: Literal["time", "fact", "time_and_fact"] = Field(default="time")
    
    llm_params: Dict[str, Any] = Field(default_factory=dict)
    embedding_params: Dict[str, Any] = Field(default_factory=dict)
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
    storage_type: Optional[str] = Field(None, max_length=50)
    llm: Optional[str] = Field(None)
    embedding: Optional[str] = Field(None)
    rerank: Optional[str] = Field(None)
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
    config_data: Dict[str, Any], config_id: Optional[int] = None
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
    workspace_data: Dict[str, Any], config_id: Optional[int] = None
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
    model_data: Dict[str, Any], config_id: Optional[int] = None
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


@dataclass(frozen=True)
class MemoryConfig:
    """Immutable memory configuration loaded from database."""
    
    config_id: int
    config_name: str
    workspace_id: UUID
    workspace_name: str
    tenant_id: UUID
    
    embedding_model_id: UUID
    embedding_model_name: str
    llm_model_id: UUID
    llm_model_name: str
    
    storage_type: str
    
    chunker_strategy: str
    reflexion_enabled: bool
    reflexion_iteration_period: int
    reflexion_range: str
    reflexion_baseline: str
    
    loaded_at: datetime
    
    rerank_model_id: Optional[UUID] = None
    rerank_model_name: Optional[str] = None
    
    llm_params: Dict[str, Any] = field(default_factory=dict)
    embedding_params: Dict[str, Any] = field(default_factory=dict)
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
    pruning_enabled: bool = False
    pruning_scene: Optional[str] = "education"
    pruning_threshold: float = 0.5
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.config_name or not self.config_name.strip():
            raise InvalidConfigError("Configuration name cannot be empty")
        
        if not self.embedding_model_id:
            raise InvalidConfigError("Embedding model ID is required")
        
        if not self.llm_model_id:
            raise InvalidConfigError("LLM model ID is required")
    
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
    
    def get_model_summary(self) -> Dict[str, Optional[str]]:
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
