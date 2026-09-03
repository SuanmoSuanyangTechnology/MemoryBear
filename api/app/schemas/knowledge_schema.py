from pydantic import BaseModel, Field, field_serializer, ConfigDict
import datetime
import uuid
from .user_schema import User
from .model_schema import ModelConfig
from typing import Any, Optional
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
        return to_timestamp_ms(dt)
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


PUBLIC_KNOWLEDGE_MODEL_FIELDS = frozenset({"embedding", "reranker", "llm", "image2text"})
PUBLIC_MODEL_FORBIDDEN_FIELDS = frozenset({"api_keys", "api_key", "api_base", "config"})


def _project_public_model(model: Any) -> Any:
    if not isinstance(model, dict):
        return model
    return {
        key: value
        for key, value in model.items()
        if key not in PUBLIC_MODEL_FORBIDDEN_FIELDS
    }


def _project_public_knowledge_node(node: Any) -> Any:
    """过滤单个知识库节点；不递归扫描 parser_config 等业务配置。"""
    if not isinstance(node, dict):
        return node

    projected = dict(node)
    for field in PUBLIC_KNOWLEDGE_MODEL_FIELDS:
        if field in projected:
            projected[field] = _project_public_model(projected[field])
    if isinstance(projected.get("children"), list):
        projected["children"] = [
            _project_public_knowledge_node(child)
            for child in projected["children"]
        ]
    return projected


def project_public_knowledge(response: Any) -> Any:
    """过滤 V1 知识库响应，保留非模型字段及管理端原始响应。"""
    if not isinstance(response, dict):
        return response

    projected = dict(response)
    data = projected.get("data")
    if not isinstance(data, dict):
        return projected

    public_data = dict(data)
    if isinstance(public_data.get("items"), list):
        public_data["items"] = [
            _project_public_knowledge_node(item)
            for item in public_data["items"]
        ]
    else:
        public_data = _project_public_knowledge_node(public_data)
    projected["data"] = public_data
    return projected
