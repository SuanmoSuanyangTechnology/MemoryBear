"""Knowledge request and response schemas copied from the legacy API."""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from ...models.owned import KnowledgeType, PermissionType
from ...models.references import LoadBalanceStrategy, ModelType
from ...utils.datetime_utils import to_timestamp_ms


class UserSummary(BaseModel):
    """Legacy user response fields required by Knowledge and Share responses."""

    id: uuid.UUID
    username: str
    email: str
    is_active: bool
    is_superuser: bool
    created_at: int
    last_login_at: int | None = None
    current_workspace_id: uuid.UUID | None = None
    current_workspace_name: str | None = None
    role: str | None = None
    preferred_language: str | None = "zh"
    phone: str | None = None
    permissions: list[str] | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", "last_login_at", mode="before")
    @classmethod
    def _datetime_to_ms(cls, value: object) -> object:
        if isinstance(value, datetime.datetime):
            return to_timestamp_ms(value)
        if isinstance(value, (int, float)):
            return int(value)
        return value


class ModelConfigSummary(BaseModel):
    """Legacy model fields without API key objects or credentials."""

    id: uuid.UUID
    name: str
    type: ModelType
    logo: str | None = None
    description: str | None = None
    provider: str
    config: dict | None = {}
    is_active: bool = True
    is_public: bool = False
    load_balance_strategy: str | None = LoadBalanceStrategy.NONE.value
    capability: list[str] = Field(default_factory=list)
    is_omni: bool = False
    model_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    is_deprecated: bool = False

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", when_used="json")
    def _serialize_time(self, value: datetime.datetime) -> int | None:
        return to_timestamp_ms(value)


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
    created_user: UserSummary
    embedding: ModelConfigSummary | None = None
    reranker: ModelConfigSummary | None = None
    llm: ModelConfigSummary | None = None
    image2text: ModelConfigSummary | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at", when_used="json")
    def _serialize_time(self, value: datetime.datetime) -> int | None:
        return to_timestamp_ms(value)


PUBLIC_KNOWLEDGE_MODEL_FIELDS = frozenset({"embedding", "reranker", "llm", "image2text"})
PUBLIC_MODEL_FORBIDDEN_FIELDS = frozenset({"api_keys", "api_key", "api_base", "config"})


def _project_public_model(model: object) -> object:
    if not isinstance(model, dict):
        return model
    return {
        key: value
        for key, value in model.items()
        if key not in PUBLIC_MODEL_FORBIDDEN_FIELDS
    }


def project_public_knowledge_data(data: dict) -> dict:
    """Copy one knowledge node and remove only model credential/config fields."""
    public = dict(data)
    for field in PUBLIC_KNOWLEDGE_MODEL_FIELDS:
        if field in public:
            public[field] = _project_public_model(public[field])
    if isinstance(public.get("children"), list):
        public["children"] = [
            project_public_knowledge_data(child)
            for child in public["children"]
        ]
    return public
