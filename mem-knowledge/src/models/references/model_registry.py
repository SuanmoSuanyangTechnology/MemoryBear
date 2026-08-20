"""Read-only model registry projections used by the shared model runtime."""

import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID

from ...utils.datetime_utils import utcnow_naive
from .base import ReferenceBase


class ModelType(StrEnum):
    LLM = "llm"
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    IMAGE = "image"
    VIDEO = "video"


class ModelProvider(StrEnum):
    OPENAI = "openai"
    SPEEDBEAR = "speedbear"
    DASHSCOPE = "dashscope"
    OLLAMA = "ollama"
    XINFERENCE = "xinference"
    GPUSTACK = "gpustack"
    BEDROCK = "bedrock"
    VOLCANO = "volcano"
    COMPOSITE = "composite"


class LoadBalanceStrategy(StrEnum):
    ROUND_ROBIN = "round_robin"
    NONE = "none"


model_config_api_key_association = Table(
    "model_config_api_key_association",
    ReferenceBase.metadata,
    Column(
        "model_config_id",
        UUID(as_uuid=True),
        ForeignKey("model_configs.id"),
        primary_key=True,
    ),
    Column(
        "api_key_id",
        UUID(as_uuid=True),
        ForeignKey("model_api_keys.id"),
        primary_key=True,
    ),
    Column("created_at", DateTime, default=utcnow_naive),
)


class ModelConfig(ReferenceBase):
    __tablename__ = "model_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    created_at = Column(DateTime, default=utcnow_naive, comment="created at")
    updated_at = Column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
        comment="updated at",
    )
    is_active = Column(Boolean, default=True, nullable=False, comment="active")
    model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("model_bases.id"),
        nullable=True,
        index=True,
        comment="base model id",
    )
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="tenant id")
    name = Column(String, nullable=False, comment="display name")
    provider = Column(
        String,
        nullable=False,
        comment="provider",
        server_default=ModelProvider.COMPOSITE,
    )
    type = Column(String, nullable=False, index=True, comment="model type")
    is_composite = Column(
        Boolean,
        default=False,
        server_default="true",
        nullable=False,
        comment="composite model",
    )
    capability = Column(
        ARRAY(String),
        default=list,
        nullable=False,
        server_default=text("'{}'::varchar[]"),
        comment="model capabilities",
    )
    is_omni = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
        comment="omni model",
    )
    config = Column(JSON, comment="model configuration")
    is_public = Column(Boolean, default=False, nullable=False, comment="public model")
    load_balance_strategy = Column(
        String,
        nullable=True,
        comment="load balancing strategy",
        default=LoadBalanceStrategy.NONE,
        server_default=LoadBalanceStrategy.NONE,
    )


class ModelApiKey(ReferenceBase):
    __tablename__ = "model_api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    created_at = Column(DateTime, default=utcnow_naive, comment="created at")
    updated_at = Column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
        comment="updated at",
    )
    is_active = Column(Boolean, default=True, nullable=False, comment="active")
    model_name = Column(String, nullable=False, comment="runtime model name")
    description = Column(String, comment="description")
    provider = Column(String, nullable=False, comment="provider")
    api_key = Column(String, nullable=False, comment="API credential")
    api_base = Column(String, comment="API base URL")
    capability = Column(
        ARRAY(String),
        default=list,
        nullable=False,
        server_default=text("'{}'::varchar[]"),
        comment="model capabilities",
    )
    is_omni = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
        comment="omni model",
    )
    config = Column(JSON, comment="API key configuration")
    usage_count = Column(String, default="0", comment="usage count")
    last_used_at = Column(DateTime, comment="last used at")
    priority = Column(String, default="1", comment="priority")


class ModelBase(ReferenceBase):
    __tablename__ = "model_bases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    logo = Column(String(255), nullable=True, comment="logo URL")
    name = Column(String, nullable=False, comment="model name")
    type = Column(String, nullable=False, index=True, comment="model type")
    provider = Column(String, nullable=False, index=True)
    description = Column(Text, comment="description")
    is_deprecated = Column(Boolean, default=False, nullable=False, comment="deprecated")
    is_official = Column(Boolean, default=True, comment="official model")
    tags = Column(ARRAY(String), default=list, nullable=False, comment="model tags")
    add_count = Column(Integer, default=0, nullable=False, comment="add count")
    created_at = Column(DateTime, default=utcnow_naive, comment="created at")
    capability = Column(
        ARRAY(String),
        default=list,
        nullable=False,
        server_default=text("'{}'::varchar[]"),
        comment="model capabilities",
    )
    is_omni = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
        comment="omni model",
    )

    __table_args__ = (
        UniqueConstraint("name", "provider", name="uk_model_name_provider"),
    )
