from pydantic import BaseModel, Field, field_serializer, field_validator, ConfigDict
import datetime
import uuid
from .user_schema import User
from .model_schema import ModelConfig
from typing import Optional
from app.core.utils.datetime_utils import to_timestamp_ms
from app.models.knowledge_model import KnowledgeType, PermissionType


class KnowledgeBase(BaseModel):
    workspace_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    name: str
    description: str | None = None
    avatar: str | None = None
    type: KnowledgeType | None = None
    permission_id: PermissionType | None = None
    embedding_id: uuid.UUID | None = None
    reranker_id: uuid.UUID | None = None
    llm_id: uuid.UUID | None = None
    image2text_id: uuid.UUID | None = None
    doc_num: int | None = None
    chunk_num: int | None = None
    parser_id: str | None = None
    parser_config: dict | None = None
    external_id: str | None = Field(None, min_length=1, max_length=36)


class KnowledgeCreate(KnowledgeBase):
    name: str = Field(..., min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        normalized = v.strip()
        if not normalized:
            raise ValueError("知识库名称不能为空或纯空白")
        return normalized

class KnowledgeUpdate(BaseModel):
    parent_id: uuid.UUID | None = Field(None)
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None)
    avatar: str | None = Field(None)
    type: KnowledgeType | None = Field(None)
    permission_id: PermissionType | None = Field(None)
    embedding_id: uuid.UUID | None = Field(None)
    reranker_id: uuid.UUID | None = Field(None)
    llm_id: uuid.UUID | None = Field(None)
    image2text_id: uuid.UUID | None = Field(None)
    doc_num: int | None = Field(None)
    chunk_num: int | None = Field(None)
    parser_id: str | None = Field(None)
    parser_config: dict | None = Field(None)
    status: int | None = Field(None)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip()
        if not normalized:
            raise ValueError("知识库名称不能为空或纯空白")
        return normalized

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v not in (0, 1, 2):
            raise ValueError("status 只允许 0（禁用）/ 1（启用）/ 2（软删除）")
        return v


class Knowledge(KnowledgeBase):
    id: uuid.UUID
    status: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
    created_user: User
    embedding: Optional[ModelConfig] = None
    reranker: Optional[ModelConfig] = None
    llm: Optional[ModelConfig] = None
    image2text: Optional[ModelConfig] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)
