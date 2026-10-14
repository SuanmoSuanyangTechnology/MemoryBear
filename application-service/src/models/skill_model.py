"""Skill model definition"""
import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSON

from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class Skill(Base):
    """Skill model - can associate tools (builtin, MCP, custom)"""
    __tablename__ = "skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String, nullable=False, comment="技能名称")
    description = Column(Text, comment="技能描述")
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    
    # Associated tools
    tools = Column(JSON, default=list, comment="关联的工具列表")
    
    # Skill config
    config = Column(JSON, default=dict, comment="技能配置")
    
    # Dedicated prompt
    prompt = Column(Text, comment="技能专属提示词")
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False, comment="是否激活")
    is_public = Column(Boolean, default=False, nullable=False, comment="是否公开到市场")
    
    # Timestamps
    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")

    def __repr__(self):
        return f"<Skill(id={self.id}, name={self.name})>"
