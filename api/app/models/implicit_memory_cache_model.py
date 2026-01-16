"""隐性记忆缓存模型"""

import uuid
import datetime
from sqlalchemy import Column, String, Integer, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class ImplicitMemoryCache(Base):
    """隐性记忆缓存表
    
    用于缓存用户的完整隐性记忆画像，包括偏好标签、四维画像、兴趣领域和行为习惯。
    减少 LLM 调用成本，提升响应速度。
    """
    __tablename__ = "implicit_memory_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(String(255), nullable=False, unique=True, index=True, comment="用户ID")
    preferences = Column(JSON, nullable=False, comment="偏好标签列表（JSON格式）")
    portrait = Column(JSON, nullable=False, comment="四维画像对象（JSON格式）")
    interest_areas = Column(JSON, nullable=False, comment="兴趣领域分布对象（JSON格式）")
    habits = Column(JSON, nullable=False, comment="行为习惯列表（JSON格式）")
    config_id = Column(Integer, nullable=True, comment="关联的配置ID")
    generated_at = Column(DateTime, nullable=False, default=datetime.datetime.now, comment="生成时间")
    expires_at = Column(DateTime, nullable=True, comment="过期时间")
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
