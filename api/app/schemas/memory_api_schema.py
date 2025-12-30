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
    config_id: Optional[str] = Field(None, description="Memory configuration ID")
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
    config_id: Optional[str] = Field(None, description="Memory configuration ID")
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
        status: Operation status (success or failed)
        end_user_id: End user ID that was written to
    """
    status: str = Field(..., description="Operation status: success or failed")
    end_user_id: str = Field(..., description="End user ID")


class MemoryReadResponse(BaseModel):
    """Response schema for memory read operation.
    
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
