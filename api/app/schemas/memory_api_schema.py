"""Memory API Service request/response schemas.

This module defines Pydantic schemas for the Memory API Service endpoints,
including request validation and response structures for read and write operations.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class MemoryWriteRequest(BaseModel):
    """Request schema for memory write operation.
    
    Attributes:
        end_user_id: End user identifier (required)
        message: Message content to store (required)
        config_id: Optional memory configuration ID
        storage_type: Storage backend type (neo4j or rag)
        user_rag_memory_id: Optional RAG memory ID for rag storage type
    """
    end_user_id: str = Field(..., description="End user ID (required)")
    message: str = Field(..., description="Message content to store")
    config_id: str = Field(..., description="Memory configuration ID (required)")
    storage_type: str = Field("neo4j", description="Storage type: neo4j or rag")
    user_rag_memory_id: Optional[str] = Field(None, description="RAG memory ID")

    @field_validator("end_user_id")
    @classmethod
    def validate_end_user_id(cls, v: str) -> str:
        """Validate that end_user_id is not empty."""
        if not v or not v.strip():
            raise ValueError("end_user_id is required and cannot be empty")
        return v.strip()

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        """Validate that message is not empty."""
        if not v or not v.strip():
            raise ValueError("message is required and cannot be empty")
        return v

    @field_validator("storage_type")
    @classmethod
    def validate_storage_type(cls, v: str) -> str:
        """Validate that storage_type is either neo4j or rag."""
        valid_types = {"neo4j", "rag"}
        if v.lower() not in valid_types:
            raise ValueError(f"storage_type must be one of: {', '.join(valid_types)}")
        return v.lower()


class MemoryReadRequest(BaseModel):
    """Request schema for memory read operation.
    
    Attributes:
        end_user_id: End user identifier (required)
        message: Query message (required)
        search_switch: Search mode (0=verify, 1=direct, 2=context)
        config_id: Optional memory configuration ID
        storage_type: Storage backend type (neo4j or rag)
        user_rag_memory_id: Optional RAG memory ID for rag storage type
    """
    end_user_id: str = Field(..., description="End user ID (required)")
    message: str = Field(..., description="Query message")
    search_switch: str = Field(
        "0", 
        description="Search mode: 0=verify, 1=direct, 2=context"
    )
    config_id: str = Field(..., description="Memory configuration ID (required)")
    storage_type: str = Field("neo4j", description="Storage type: neo4j or rag")
    user_rag_memory_id: Optional[str] = Field(None, description="RAG memory ID")

    @field_validator("end_user_id")
    @classmethod
    def validate_end_user_id(cls, v: str) -> str:
        """Validate that end_user_id is not empty."""
        if not v or not v.strip():
            raise ValueError("end_user_id is required and cannot be empty")
        return v.strip()

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        """Validate that message is not empty."""
        if not v or not v.strip():
            raise ValueError("message is required and cannot be empty")
        return v

    @field_validator("storage_type")
    @classmethod
    def validate_storage_type(cls, v: str) -> str:
        """Validate that storage_type is either neo4j or rag."""
        valid_types = {"neo4j", "rag"}
        if v.lower() not in valid_types:
            raise ValueError(f"storage_type must be one of: {', '.join(valid_types)}")
        return v.lower()

    @field_validator("search_switch")
    @classmethod
    def validate_search_switch(cls, v: str) -> str:
        """Validate that search_switch is a valid mode."""
        valid_modes = {"0", "1", "2"}
        if v not in valid_modes:
            raise ValueError(f"search_switch must be one of: {', '.join(valid_modes)}")
        return v


class MemoryWriteResponse(BaseModel):
    """Response schema for memory write operation.
    
    Attributes:
        task_id: Celery task ID for status polling
        status: Initial task status (PENDING)
        end_user_id: End user ID the write was submitted for
    """
    task_id: str = Field(..., description="Celery task ID for polling")
    status: str = Field(..., description="Task status: PENDING")
    end_user_id: str = Field(..., description="End user ID")


class TaskStatusResponse(BaseModel):
    """Response schema for task status check.
    
    Attributes:
        status: Task status (PENDING, STARTED, SUCCESS, FAILURE, SKIPPED)
        result: Task result data (available when status is SUCCESS or FAILURE)
    """
    status: str = Field(..., description="Task status")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result when completed")


class MemoryWriteSyncResponse(BaseModel):
    """Response schema for synchronous memory write.
    
    Attributes:
        status: Operation status (success or failed)
        end_user_id: End user ID that was written to
    """
    status: str = Field(..., description="Operation status: success or failed")
    end_user_id: str = Field(..., description="End user ID")


class MemoryReadSyncResponse(BaseModel):
    """Response schema for synchronous memory read.
    
    Attributes:
        answer: Generated answer from memory retrieval
        intermediate_outputs: Intermediate retrieval outputs
        end_user_id: End user ID that was queried
    """
    answer: str = Field(..., description="Generated answer")
    intermediate_outputs: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Intermediate retrieval outputs"
    )
    end_user_id: str = Field(..., description="End user ID")


class MemoryReadResponse(BaseModel):
    """Response schema for memory read operation.
    
    Attributes:
        task_id: Celery task ID for status polling
        status: Initial task status (PENDING)
        end_user_id: End user ID the read was submitted for
    """
    task_id: str = Field(..., description="Celery task ID for polling")
    status: str = Field(..., description="Task status: PENDING")
    end_user_id: str = Field(..., description="End user ID")


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


class ListConfigsResponse(BaseModel):
    """Response schema for listing memory configs.
    
    Attributes:
        configs: List of memory config items
        total: Total number of configs
    """
    configs: List[MemoryConfigItem] = Field(default_factory=list, description="List of configs")
    total: int = Field(0, description="Total number of configs")
