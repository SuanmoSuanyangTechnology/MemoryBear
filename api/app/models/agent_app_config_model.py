import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.base.type import PydanticType
from app.db import Base
from app.schemas.app_schema import ModelParameters


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 一对一关联到 App
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, unique=True, index=True)

    # Agent 行为配置
    system_prompt = Column(Text, nullable=True, comment="系统提示词")
    default_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True, index=True, comment="默认模型配置ID")

    # 结构化配置（直接存储 JSON）
    # model_parameters = Column(JSON, nullable=True, comment="模型参数配置（temperature、max_tokens等）")
    model_parameters = Column(PydanticType(ModelParameters), nullable=True,
                              comment="模型参数配置（temperature、max_tokens等）")

    knowledge_retrieval = Column(JSON, nullable=True, comment="知识库检索配置")
    memory = Column(JSON, nullable=True, comment="记忆配置")
    variables = Column(JSON, default=list, nullable=True, comment="变量配置")
    tools = Column(JSON, default=dict, nullable=True, comment="工具配置")

    # 多 Agent 相关字段
    agent_role = Column(String(20), comment="Agent 角色: master|sub|standalone")
    agent_domain = Column(String(50), comment="专业领域: customer_service|technical_support|sales 等")
    parent_agent_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id"), comment="父 Agent ID")
    capabilities = Column(JSON, default=list, comment="Agent 能力列表")

    # 状态与时间戳
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # 关系
    app = relationship("App", back_populates="agent_config")
    parent_agent = relationship("AgentConfig", remote_side=[id], backref="sub_agents")

    def __repr__(self):
        return f"<AgentConfig(id={self.id}, app_id={self.app_id})>"
