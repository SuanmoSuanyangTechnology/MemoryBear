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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import relationship

from ...utils.datetime_utils import utcnow_naive
from .base import ReferenceBase


class ModelType(StrEnum):
    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    IMAGE = "image"
    VIDEO = "video"
    ASR = "asr"

    @classmethod
    def _missing_(cls, value):
        """存量字符串读侧归一：`"chat"` → LLM（DB 旧行兼容）；`"asr"` → ASR（大小写容忍）。"""
        if isinstance(value, str):
            if value.lower() == "chat":
                return cls.LLM
            if value.lower() == "asr":
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
