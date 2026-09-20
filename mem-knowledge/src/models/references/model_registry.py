"""Read-only model registry projections used by the shared model runtime."""

import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSON, JSONB, UUID
from sqlalchemy.orm import relationship

from ...utils.datetime_utils import utcnow_naive
from .base import ReferenceBase


class ModelType(StrEnum):
    LLM = "llm"
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    IMAGE = "image"
    VIDEO = "video"
    ASR = "asr"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str) and value.lower() == "asr":
            return cls.ASR
        return None


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
    logo = Column(String(255), nullable=True, comment="model logo URL")
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
    description = Column(String, comment="model description")
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
    input_modalities = Column(
        ARRAY(String),
        default=list,
        nullable=False,
        server_default=text("'{}'::varchar[]"),
        comment="输入模态（如['text','image','audio','video']）",
    )
    output_modalities = Column(
        ARRAY(String),
        default=list,
        nullable=False,
        server_default=text("'{}'::varchar[]"),
        comment="输出模态（如['text','image','audio']）",
    )
    features = Column(
        ARRAY(String),
        default=list,
        nullable=False,
        server_default=text("'{}'::varchar[]"),
        comment="能力特征（如['thinking','json_output','function_call']）",
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

    # 只读投影：快照构建派生 is_deprecated（无写语义，故无 core ORM 的 back_populates/cascade）
    model_base = relationship("ModelBase")


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


class ModelChannel(ReferenceBase):
    """Read-only projection of a model channel row (M5 起平台/租户凭据落表处).

    列集与 core `ModelChannel` 对齐：渠道池投影需要覆盖匹配与排序字段
    （model_names/priority/created_at），另带凭据指纹/掩码供 ChannelSnapshot 构造。
    """

    __tablename__ = "model_channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    model_names = Column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    api_base = Column(String(512), nullable=True)
    credential_encrypted = Column(Text, nullable=False)
    credential_sha256 = Column(String(64), nullable=False, server_default="")
    credential_masked = Column(String(255), nullable=False, server_default="")
    priority = Column(Integer, nullable=False, default=0, server_default="0")
    cooldown_until_ms = Column(BigInteger, nullable=True)
    source = Column(String(20), nullable=False, default="manual")
    extra = Column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive, comment="created at")
    updated_at = Column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="updated at"
    )


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
