"""情绪建议缓存模型"""

import uuid
import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base


class EmotionSuggestionsCache(Base):
    """情绪建议缓存表
    
    用于缓存个性化情绪建议，减少 LLM 调用成本，提升响应速度。
    """
    __tablename__ = "emotion_suggestions_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    end_user_id = Column(String(255), nullable=False, unique=True, index=True, comment="终端用户ID（组ID）")
    health_summary = Column(Text, nullable=False, comment="健康状态摘要")
    suggestions = Column(JSON, nullable=False, comment="建议列表（JSON格式）")
    generated_at = Column(DateTime, nullable=False, default=datetime.datetime.now, comment="生成时间")
    expires_at = Column(DateTime, nullable=True, comment="过期时间")
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
