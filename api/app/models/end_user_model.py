import datetime
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, BigInteger
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
    
    # 用户基本信息字段
    name = Column(String, nullable=True, comment="姓名")
    position = Column(String, nullable=True, comment="职位")
    department = Column(String, nullable=True, comment="部门")
    contact = Column(String, nullable=True, comment="联系方式")
    phone = Column(String, nullable=True, comment="电话")
    hire_date = Column(BigInteger, nullable=True, comment="入职日期（时间戳，毫秒）")
    updatetime_profile = Column(BigInteger, nullable=True, comment="核心档案信息最后更新时间（时间戳，毫秒）")
    
    # 缓存字段 - Cache fields for pre-computed analytics
    memory_insight = Column(Text, nullable=True, comment="缓存的记忆洞察报告")
    user_summary = Column(Text, nullable=True, comment="缓存的用户摘要")
    memory_insight_updated_at = Column(DateTime, nullable=True, comment="洞察报告最后更新时间")
    user_summary_updated_at = Column(DateTime, nullable=True, comment="用户摘要最后更新时间")

    # 与 App 的反向关系
    app = relationship(
        "App",
        back_populates="end_users"
    )