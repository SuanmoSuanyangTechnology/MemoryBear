import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, JSONB, UUID
from sqlalchemy.orm import relationship

from ..utils.datetime_utils import utcnow_naive
from .cross_domain_base import CrossDomainBase as Base


class BaseModel(Base):
    """Base model (abstract, extracts shared columns)."""
    __abstract__ = True  # Marked abstract; no table is created
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")


class ModelType(StrEnum):
    """Model type enum."""
    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    ASR = "asr"
    # TTS = "tts"
    # SPEECH2TEXT = "speech2text"
    IMAGE = "image"
    # AUDIO = "audio"
    VIDEO = "video"

    @classmethod
    def _missing_(cls, value):
        """Read-side normalization of legacy strings: `"chat"` → LLM (compatibility with old DB/YAML rows); `"asr"` → ASR (case-tolerant)."""
        if isinstance(value, str):
            if value.lower() == "chat":
                return cls.LLM
            if value.lower() == "asr":
                return cls.ASR
        return None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        schema["enum"] = list(dict.fromkeys([*schema.get("enum", []), "asr"]))
        return schema


# Read-side compatibility for legacy types (the CHAT member is gone; old YAML/old images may still
# pass or store "chat"): always use this constant for set/SQL comparisons, never ModelType.CHAT
LEGACY_CHAT_TYPE = "chat"
# LLM family (including legacy chat): shared by set checks and SQL IN; tuple order defines the SQL literal order
LLM_FAMILY_TYPES = (ModelType.LLM.value, LEGACY_CHAT_TYPE)


class ModelCapability(StrEnum):
    """Deprecated (split into three columns by contract v2; kept during the read-side legacy-column compatibility window; removed together with the columns)"""
    VISION = "vision"
    AUDIO = "audio"
    VIDEO = "video"
    THINKING = "thinking"
    THINKING_ONLY = "thinking_only"
    JSON_OUTPUT = "json_output"
    FUNCTION_CALL = "function_call"


class Modality(StrEnum):
    """Modality (value domain of the contract v2 input/output_modalities columns, same semantics as the redbear-model package)"""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class ModelFeature(StrEnum):
    """Model features (value domain of the contract v2 features column, same semantics as the redbear-model package)"""
    THINKING = "thinking"
    THINKING_ONLY = "thinking_only"
    JSON_OUTPUT = "json_output"
    FUNCTION_CALL = "function_call"


class ModelProvider(StrEnum):
    """Model provider enum."""
    OPENAI = "openai"
    SPEEDBEAR = "speedbear"
    MINIMAX = "minimax"
    # ANTHROPIC = "anthropic"
    # GOOGLE = "google"
    # BAIDU = "baidu"
    DASHSCOPE = "dashscope"
    # ZHIPU = "zhipu"
    # MOONSHOT = "moonshot"
    # DEEPSEEK = "deepseek"
    OLLAMA = "ollama"
    XINFERENCE = "xinference"
    GPUSTACK = "gpustack"
    BEDROCK = "bedrock"
    VOLCANO = "volcano"
    COMPOSITE = "composite"


class LoadBalanceStrategy(StrEnum):
    """API key load-balancing strategy enum."""
    ROUND_ROBIN = "round_robin"  # Round-robin
    NONE = "none"  # None


# Transitional entity (no further changes; removed by a separate migration once the
# channel-resolution switch is stable)
# Many-to-many association table
model_config_api_key_association = Table(
    'model_config_api_key_association',
    Base.metadata,
    Column('model_config_id', UUID(as_uuid=True), ForeignKey('model_configs.id'), primary_key=True),
    Column('api_key_id', UUID(as_uuid=True), ForeignKey('model_api_keys.id'), primary_key=True),
    Column('created_at', DateTime, default=utcnow_naive)
)


class ModelConfig(BaseModel):
    """Model config table."""
    __tablename__ = "model_configs"

    model_id = Column(UUID(as_uuid=True), ForeignKey("model_bases.id"), nullable=True, index=True, comment="基础模型ID")
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    logo = Column(String(255), nullable=True, comment="模型logo图片URL")
    name = Column(String, nullable=False, comment="模型显示名称")
    provider = Column(String, nullable=False, comment="供应商", server_default=ModelProvider.COMPOSITE)
    type = Column(String, nullable=False, index=True, comment="模型类型")
    is_composite = Column(Boolean, default=False, server_default="false", nullable=False, comment="是否为组合模型")
    description = Column(String, comment="模型描述")
    
    # Model config parameters
    capability = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                        comment="模型能力列表（如['vision', 'audio', 'video', 'thinking']）")
    is_omni = Column(Boolean, default=False, nullable=False, server_default="false", comment="是否为Omni模型（使用特殊API调用）")
    input_modalities = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                              comment="输入模态（如['text','image','audio','video']）")
    output_modalities = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                               comment="输出模态（如['text','image','audio']）")
    features = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                      comment="能力特征（如['thinking','json_output','function_call']）")
    config = Column(JSON, comment="模型配置参数")
    # - temperature: controls randomness of generated text. Higher = more random/creative; lower = more deterministic
    # - top_p: an alternative sampling method to temperature; limits how broadly the model picks from top-probability words
    # - presence_penalty: penalizes newly introduced topics, encouraging the model to keep to themes already mentioned
    # - frequency_penalty: penalizes high-frequency words, reducing verbatim repetition
    # - stop / stop_sequences: one or more string sequences at which the model stops generating
    # - provider-specific parameters: e.g. a stream (streaming output) switch or seed (random seed) some models may support

    # # Model capability parameters
    # max_tokens = Column(String, comment="最大token数")
    # context_length = Column(String, comment="上下文长度")

    # State management
    is_public = Column(Boolean, default=False, nullable=False, comment="是否公开")
    load_balance_strategy = Column(String, nullable=True, comment="负载均衡策略", default=LoadBalanceStrategy.NONE,
                                   server_default=LoadBalanceStrategy.NONE)
    
    # Relationships
    model_base = relationship("ModelBase", back_populates="configs")
    api_keys = relationship(
        "ModelApiKey",
        secondary=model_config_api_key_association,
        back_populates="model_configs"
    )

    def __repr__(self):
        return f"<ModelConfig(id={self.id}, name={self.name}, type={self.type})>"


# Transitional entity (no further changes; removed by a separate migration once the
# channel-resolution switch is stable)
class ModelApiKey(BaseModel):
    """Model API key table."""
    __tablename__ = "model_api_keys"
    
    # API key info
    model_name = Column(String, nullable=False, comment="模型实际名称")
    description = Column(String, comment="备注")
    provider = Column(String, nullable=False, comment="API Key提供商")
    api_key = Column(String, nullable=False, comment="API密钥")
    api_base = Column(String, comment="API基础URL")
    
    # Model capability parameters
    capability = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                        comment="模型能力列表（如['vision', 'audio', 'video']）")
    is_omni = Column(Boolean, default=False, nullable=False, server_default="false", comment="是否为Omni模型（使用特殊API调用）")
    
    # Configuration parameters
    config = Column(JSON, comment="API Key特定配置")
    
    # Usage statistics
    usage_count = Column(String, default="0", comment="使用次数")
    last_used_at = Column(DateTime, comment="最后使用时间")
    
    # State management
    priority = Column(String, default="1", comment="优先级")

    # Usage-event attribution pass-through: filled in by ModelApiKeyService when returning the runtime
    # shell; not ORM columns and not part of flush; consumers pass them through to RedBearModelConfig
    tenant_id = None
    model_config_id = None
    channel_id = None

    # Capability carrier (contract v2 three columns): the legacy table lacks these columns; the
    # runtime shell is filled by ModelApiKeyService from the profile; non-mapped attributes, never
    # persisted; consumers read them the same way as RedBearModelConfig
    input_modalities = None
    output_modalities = None
    features = None

    # In-request channel failover plan: a transient non-mapped attribute, never persisted or
    # serialized; attached by ModelApiKeyService when returning the runtime shell; consumers pass it
    # through to RedBearModelConfig
    failover_plan = None

    # Relationships
    model_configs = relationship(
        "ModelConfig",
        secondary=model_config_api_key_association,
        back_populates="api_keys"
    )


    def __repr__(self):
        return f"<ModelApiKey(id={self.id}, model_name={self.model_name}, provider={self.provider})>"


class ModelBase(Base):
    """Base model info table (model plaza)."""
    __tablename__ = "model_bases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    logo = Column(String(255), nullable=True, comment="模型logo图片URL")
    name = Column(String, nullable=False, comment="模型唯一标识（如gpt-3.5-turbo）")
    type = Column(String, nullable=False, index=True, comment="模型类型")
    provider = Column(String, nullable=False, index=True)
    description = Column(Text, comment="模型描述")
    is_deprecated = Column(Boolean, default=False, nullable=False, comment="是否弃用")
    is_official = Column(Boolean, default=True, comment="是否供应商官方模型（区分自定义）")
    tags = Column(ARRAY(String), default=list, nullable=False, comment="模型标签（如['聊天', '创作']）")
    add_count = Column(Integer, default=0, nullable=False, comment="模型被用户添加的次数")
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间", )
    capability = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                        comment="模型能力列表（如['vision', 'audio', 'video']）")
    is_omni = Column(Boolean, default=False, nullable=False, server_default="false", comment="是否为Omni模型（使用特殊API调用）")
    input_modalities = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                              comment="输入模态（如['text','image','audio','video']）")
    output_modalities = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                               comment="输出模态（如['text','image','audio']）")
    features = Column(ARRAY(String), default=list, nullable=False, server_default=text("'{}'::varchar[]"),
                      comment="能力特征（如['thinking','json_output','function_call']）")

    # Relationships
    configs = relationship("ModelConfig", back_populates="model_base", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("name", "provider", name="uk_model_name_provider"),
    )

    def __repr__(self):
        return f"<ModelBase(name={self.name}, provider={self.provider}, type={self.type})>"


class ModelChannel(BaseModel):
    """Channel credential registry (the convergence target for model_api_keys).

    Inherits BaseModel for id/created_at/updated_at/is_active; soft-disable = is_active=False
    (affects new resolutions only).
    """
    __tablename__ = "model_channels"

    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, comment="凭据归属租户")
    provider = Column(String(50), nullable=False, comment="供应商（不允许 composite）")
    model_names = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"), comment="覆盖模型集；[]=provider 级默认渠道")
    api_base = Column(String(512), nullable=True, comment="执行端点；空=provider 默认 base_url（不参与覆盖匹配）")
    credential_encrypted = Column(Text, nullable=False, comment="信封 v{ver}:iv:tag:ct")
    credential_sha256 = Column(String(64), nullable=False, comment="凭据指纹（幂等合并键）")
    credential_masked = Column(String(255), nullable=False, comment="展示用掩码 sk-****abcd")
    priority = Column(Integer, nullable=False, default=0, server_default="0", comment="同精确度主备权重")
    cooldown_until_ms = Column(BigInteger, nullable=True, comment="熔断预留（阶段一恒空）")
    source = Column(String(20), nullable=False, default="manual", server_default="manual", comment="manual|platform")
    extra = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"), comment="speedbear 等企业语义")
    remark = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=True, comment="登记人弱引用（不建 FK）")

    __table_args__ = (
        UniqueConstraint("provider", "tenant_id", "api_base", "credential_sha256",
                         name="uq_channel_credential", postgresql_nulls_not_distinct=True),
        Index("ix_channel_tenant_provider", "tenant_id", "provider"),
    )

    def __repr__(self):
        return f"<ModelChannel(id={self.id}, provider={self.provider}, tenant_id={self.tenant_id})>"
