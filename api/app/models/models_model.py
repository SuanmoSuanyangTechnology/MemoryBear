import datetime
import uuid
from enum import StrEnum

from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey, Enum as SQLEnum, UniqueConstraint, Integer, ARRAY, Table
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base


class BaseModel(Base):
    """基础模型（抽象类，提取公共字段）"""
    __abstract__ = True  # 标记为抽象类，不生成表
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")


class ModelType(StrEnum):
    """模型类型枚举"""
    LLM = "llm"
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    TTS = "tts"
    SPEECH2TEXT = "speech2text"
    # IMAGE = "image"
    # AUDIO = "audio"
    # VISION = "vision"


class ModelProvider(StrEnum):
    """模型提供商枚举"""
    OPENAI = "openai"
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
    COMPOSITE = "composite"


class LoadBalanceStrategy(StrEnum):
    """API Key负载均衡策略枚举"""
    ROUND_ROBIN = "round_robin"  # 轮询
    NONE = "none"  # 无


# 多对多关联表
model_config_api_key_association = Table(
    'model_config_api_key_association',
    Base.metadata,
    Column('model_config_id', UUID(as_uuid=True), ForeignKey('model_configs.id'), primary_key=True),
    Column('api_key_id', UUID(as_uuid=True), ForeignKey('model_api_keys.id'), primary_key=True),
    Column('created_at', DateTime, default=datetime.datetime.now)
)


class ModelConfig(BaseModel):
    """模型配置表"""
    __tablename__ = "model_configs"

    model_id = Column(UUID(as_uuid=True), ForeignKey("model_bases.id"), nullable=True, index=True, comment="基础模型ID")
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    logo = Column(String(255), nullable=True, comment="模型logo图片URL")
    name = Column(String, nullable=False, comment="模型显示名称")
    provider = Column(String, nullable=False, comment="供应商", server_default=ModelProvider.COMPOSITE)
    type = Column(String, nullable=False, index=True, comment="模型类型")
    is_composite = Column(Boolean, default=False, server_default="true", nullable=False, comment="是否为组合模型")
    description = Column(String, comment="模型描述")
    
    # 模型配置参数
    config = Column(JSON, comment="模型配置参数")
    # - temperature : 控制生成文本的随机性。值越高，输出越随机、越有创造性；值越低，输出越确定、越保守。
    # - top_p : 一种替代 temperature 的采样方法，控制模型从概率最高的词中选择的范围。
    # - presence_penalty : 对新出现的主题进行惩罚，鼓励模型谈论已经提到过的话题。
    # - frequency_penalty : 对高频词进行惩罚，降低重复相同词语的可能性。
    # - stop 或 stop_sequences : 一个或多个字符串序列，当模型生成这些序列时会停止输出。
    # - 特定于提供商的参数 : 比如某些模型可能支持的 stream (流式输出) 开关、 seed (随机种子) 等。
    
    # # 模型能力参数
    # max_tokens = Column(String, comment="最大token数")
    # context_length = Column(String, comment="上下文长度")
    
    # 状态管理
    is_public = Column(Boolean, default=False, nullable=False, comment="是否公开")
    load_balance_strategy = Column(String, nullable=True, comment="负载均衡策略", default=LoadBalanceStrategy.NONE,
                                   server_default=LoadBalanceStrategy.NONE)
    
    # 关联关系
    model_base = relationship("ModelBase", back_populates="configs")
    api_keys = relationship(
        "ModelApiKey",
        secondary=model_config_api_key_association,
        back_populates="model_configs"
    )

    def __repr__(self):
        return f"<ModelConfig(id={self.id}, name={self.name}, type={self.type})>"


class ModelApiKey(BaseModel):
    """模型API密钥表"""
    __tablename__ = "model_api_keys"
    
    # API Key 信息
    model_name = Column(String, nullable=False, comment="模型实际名称")
    description = Column(String, comment="备注")
    provider = Column(String, nullable=False, comment="API Key提供商")
    api_key = Column(String, nullable=False, comment="API密钥")
    api_base = Column(String, comment="API基础URL")
    
    # 配置参数
    config = Column(JSON, comment="API Key特定配置")
    
    # 使用统计
    usage_count = Column(String, default="0", comment="使用次数")
    last_used_at = Column(DateTime, comment="最后使用时间")
    
    # 状态管理
    priority = Column(String, default="1", comment="优先级")

    # 关联关系
    model_configs = relationship(
        "ModelConfig",
        secondary=model_config_api_key_association,
        back_populates="api_keys"
    )


    def __repr__(self):
        return f"<ModelApiKey(id={self.id}, model_name={self.model_name}, provider={self.provider})>"


class ModelBase(Base):
    """基础模型信息表（模型广场）"""
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
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间", server_default=func.now())

    # 关联关系
    configs = relationship("ModelConfig", back_populates="model_base", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("name", "provider", name="uk_model_name_provider"),
    )

    def __repr__(self):
        return f"<ModelBase(name={self.name}, provider={self.provider}, type={self.type})>"