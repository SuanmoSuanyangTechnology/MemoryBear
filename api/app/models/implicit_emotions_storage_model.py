"""
Implicit Emotions Storage Model

数据库模型：存储用户的隐性记忆画像和情绪建议数据
替代原有的Redis缓存方式
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db import Base


class ImplicitEmotionsStorage(Base):
    """隐性记忆和情绪存储表"""
    
    __tablename__ = "implicit_emotions_storage"
    
    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, comment="主键ID")
    
    # 用户标识
    end_user_id = Column(String(255), nullable=False, unique=True, index=True, comment="终端用户ID")
    
    # 隐性记忆画像数据（JSON格式）
    implicit_profile = Column(JSONB, nullable=True, comment="隐性记忆用户画像数据")
    
    # 情绪建议数据（JSON格式）
    emotion_suggestions = Column(JSONB, nullable=True, comment="情绪个性化建议数据")
    
    # 时间戳
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")
    
    # 数据生成时间（用于业务逻辑）
    implicit_generated_at = Column(DateTime, nullable=True, comment="隐性记忆画像生成时间")
    emotion_generated_at = Column(DateTime, nullable=True, comment="情绪建议生成时间")
    
    # 索引
    __table_args__ = (
        Index('idx_end_user_id', 'end_user_id'),
        Index('idx_updated_at', 'updated_at'),
    )
    
    def __repr__(self):
        return f"<ImplicitEmotionsStorage(id={self.id}, end_user_id={self.end_user_id})>"
