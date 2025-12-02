import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from app.db import Base


class ReleaseShare(Base):
    """应用发布版本分享配置"""
    __tablename__ = "release_shares"
    __table_args__ = (
        UniqueConstraint("release_id", name="uq_release_share_release_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    release_id = Column(UUID(as_uuid=True), ForeignKey("app_releases.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # 分享配置
    is_enabled = Column(Boolean, default=True, nullable=False, comment="是否启用公开分享")
    share_token = Column(String, nullable=False, unique=True, index=True, comment="公开访问的唯一标识")
    
    # 访问控制
    require_password = Column(Boolean, default=False, nullable=False, comment="是否需要密码访问")
    password_hash = Column(String, nullable=True, comment="访问密码哈希")
    
    # 嵌入配置
    allow_embed = Column(Boolean, default=False, nullable=False, comment="是否允许嵌入")
    embed_domains = Column(JSON, default=list, comment="允许嵌入的域名白名单")
    
    # 统计数据
    view_count = Column(Integer, default=0, nullable=False, comment="访问次数")
    last_accessed_at = Column(DateTime, nullable=True, comment="最后访问时间")
    
    # 元数据
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="创建者")
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # 关系
    release = relationship("AppRelease", backref="share")
    app = relationship("App")
    creator = relationship("User")

    def __repr__(self):
        return f"<ReleaseShare(id={self.id}, release_id={self.release_id}, share_token={self.share_token})>"
