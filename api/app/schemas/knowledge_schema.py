from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid
from .user_schema import User
from .model_schema import ModelConfig
from typing import Optional
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


class KnowledgeCreate(KnowledgeBase):
    pass

class KnowledgeUpdate(BaseModel):
    parent_id: uuid.UUID | None = Field(None)
    name: str | None = Field(None)
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
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
