import datetime
import uuid
from enum import StrEnum
from typing import Optional, List
from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from app.db import Base


class ModelType(StrEnum):
    """模型类型枚举"""
    LLM = "llm"
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"


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


class ModelConfig(Base):
    """模型配置表"""
    __tablename__ = "model_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    name = Column(String, nullable=False, comment="模型显示名称")
    type = Column(String, nullable=False, index=True, comment="模型类型")
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
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    is_public = Column(Boolean, default=False, nullable=False, comment="是否公开")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间") 
    
    # 关联关系
    api_keys = relationship("ModelApiKey", back_populates="model_config", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ModelConfig(id={self.id}, name={self.name}, type={self.type})>"


class ModelApiKey(Base):
    """模型API密钥表"""
    __tablename__ = "model_api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False, comment="模型配置ID")
    
    # API Key 信息
    model_name = Column(String, nullable=False, comment="模型实际名称")
    provider = Column(String, nullable=False, comment="API Key提供商")
    api_key = Column(String, nullable=False, comment="API密钥")
    api_base = Column(String, comment="API基础URL")
    
    # 配置参数
    config = Column(JSON, comment="API Key特定配置")
    
    # 使用统计
    usage_count = Column(String, default="0", comment="使用次数")
    last_used_at = Column(DateTime, comment="最后使用时间")
    
    # 状态管理
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    priority = Column(String, default="1", comment="优先级")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")
    
    # 关联关系
    model_config = relationship("ModelConfig", back_populates="api_keys")

    def __repr__(self):
        return f"<ModelApiKey(id={self.id}, model_name={self.model_name}, provider={self.provider}, model_config_id={self.model_config_id})>"
