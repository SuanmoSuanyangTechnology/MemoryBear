import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class EndUser(Base):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False)
    # end_user_id = Column(String, nullable=False, index=True)
    other_id = Column(String, nullable=True)  # Store original user_id
    other_name = Column(String, default="", nullable=False)
    other_address = Column(String, default="", nullable=False)
    reflection_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    
    memory_config_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("memory_config.config_id"), 
        nullable=True, 
        index=True, 
        comment="关联的记忆配置ID"
    )
    
    # 用户基本信息字段
    position = Column(String, nullable=True, comment="职位")
    department = Column(String, nullable=True, comment="部门")
    contact = Column(String, nullable=True, comment="联系方式")
    phone = Column(String, nullable=True, comment="电话")
    hire_date = Column(DateTime, nullable=True, comment="入职日期")
    updatetime_profile = Column(DateTime, nullable=True, comment="核心档案信息最后更新时间")
    
    # 用户摘要四个维度 - User Summary Four Dimensions
    user_summary = Column(Text, nullable=True, comment="缓存的用户摘要（基本介绍）")
    personality_traits = Column(Text, nullable=True, comment="性格特点")
    core_values = Column(Text, nullable=True, comment="核心价值观")
    one_sentence_summary = Column(Text, nullable=True, comment="一句话总结")
    user_summary_updated_at = Column(DateTime, nullable=True, comment="用户摘要最后更新时间")
    
    # 记忆洞察四个维度 - Memory Insight Four Dimensions
    memory_insight = Column(Text, nullable=True, comment="缓存的记忆洞察报告（总体概述）")
    behavior_pattern = Column(Text, nullable=True, comment="行为模式")
    key_findings = Column(Text, nullable=True, comment="关键发现")
    growth_trajectory = Column(Text, nullable=True, comment="成长轨迹")
    memory_insight_updated_at = Column(DateTime, nullable=True, comment="洞察报告最后更新时间")

    # 与 App 的反向关系
    app = relationship(
        "App",
        back_populates="end_users"
    )