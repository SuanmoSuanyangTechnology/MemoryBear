"""Skill 模型定义"""
import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSON

from app.db import Base


class Skill(Base):
    """技能模型 - 可以关联工具（内置、MCP、自定义）"""
    __tablename__ = "skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String, nullable=False, comment="技能名称")
    description = Column(Text, comment="技能描述")
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    
    # 关联的工具
    tools = Column(JSON, default=list, comment="关联的工具列表")
    
    # 技能配置
    config = Column(JSON, default=dict, comment="技能配置")
    
    # 专属提示词
    prompt = Column(Text, comment="技能专属提示词")
    
    # 状态
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    is_public = Column(Boolean, default=False, nullable=False, comment="是否公开到市场")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")

    def __repr__(self):
        return f"<Skill(id={self.id}, name={self.name})>"
