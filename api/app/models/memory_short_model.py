"""
记忆模型 - 短期记忆和长期记忆表
"""
import uuid
import datetime
from sqlalchemy import Column, String, DateTime, Text, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class ShortTermMemory(Base):
    """短期记忆表
    
    用于存储临时的对话记忆，通常保存较短时间
    """
    __tablename__ = "memory_short_term"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True, comment="记忆ID")
    
    # 用户信息
    end_user_id = Column(String(255), nullable=False, index=True, comment="终端用户ID")
    
    # 对话内容
    messages = Column(Text, nullable=False, comment="用户消息内容")
    aimessages = Column(Text, nullable=True, comment="AI回复消息内容")
    
    # 搜索开关
    search_switch = Column(String(50), nullable=True, comment="搜索开关状态")
    
    # 检索内容 - 存储为JSON格式的列表，包含字典 [{}, {}]
    retrieved_content = Column(JSON, nullable=True, default=list, comment="检索到的相关内容，格式为[{}, {}]")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, index=True, comment="创建时间")
    
    def __repr__(self):
        return f"<ShortTermMemory(id={self.id}, end_user_id={self.end_user_id}, created_at={self.created_at})>"


class LongTermMemory(Base):
    """长期记忆表
    
    用于存储重要的对话记忆，长期保存
    """
    __tablename__ = "memory_long_term"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True, comment="记忆ID")
    
    # 用户信息
    end_user_id = Column(String(255), nullable=False, index=True, comment="终端用户ID")
    
    # 检索内容 - 存储为JSON格式的列表，包含字典 [{}, {}]
    retrieved_content = Column(JSON, nullable=True, default=list, comment="检索到的相关内容，格式为[{}, {}]")
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.datetime.now, nullable=False, index=True, comment="创建时间")
    
    def __repr__(self):
        return f"<LongTermMemory(id={self.id}, end_user_id={self.end_user_id}, created_at={self.created_at})>"