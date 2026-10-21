import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from .pydantic_type import PydanticType
from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive
from .model_parameters import ModelParameters


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # One-to-one association to App
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, unique=True, index=True)

    # Agent behavior configuration
    system_prompt = Column(Text, nullable=True, comment="系统提示词")
    default_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True, index=True, comment="默认模型配置ID")

    # Structured configuration (stored directly as JSON)
    # model_parameters = Column(JSON, nullable=True, comment="模型参数配置（temperature、max_tokens等）")
    model_parameters = Column(PydanticType(ModelParameters), nullable=True,
                              comment="模型参数配置（temperature、max_tokens等）")

    knowledge_retrieval = Column(JSON, nullable=True, comment="知识库检索配置")
    memory = Column(JSON, nullable=True, comment="记忆配置")
    variables = Column(JSON, default=list, nullable=True, comment="变量配置")
    tools = Column(JSON, default=list, nullable=True, comment="工具配置")
    skills = Column(JSON, default=dict, nullable=True, comment="技能配置")
    features = Column(JSON, default=dict, nullable=True, comment="功能特性配置")

    # Multi-agent related fields
    agent_role = Column(String(20), comment="Agent 角色: master|sub|standalone")
    agent_domain = Column(String(50), comment="专业领域: customer_service|technical_support|sales 等")
    parent_agent_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id"), comment="父 Agent ID")
    capabilities = Column(JSON, default=list, comment="Agent 能力列表")

    # Status and timestamps
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    # Relationships
    app = relationship("App", back_populates="agent_config")
    parent_agent = relationship("AgentConfig", remote_side=[id], backref="sub_agents")

    def __repr__(self):
        return f"<AgentConfig(id={self.id}, app_id={self.app_id})>"
