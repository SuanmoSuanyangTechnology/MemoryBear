import datetime
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from app.db import Base
from app.models.app_model import IconType


class AppRelease(Base):
    __tablename__ = "app_releases"
    __table_args__ = (
        UniqueConstraint("app_id", "version", name="uq_app_release_app_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, index=True)

    # 版本号（按应用内递增）
    version = Column(Integer, nullable=False, default=1, index=True)
    # 版本号，显示用
    version_name = Column(String, nullable=False)
    # 版本说明
    release_notes = Column(String, nullable=True, comment="版本说明")

    # 基础信息快照（发布时冻结）
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    icon = Column(String, nullable=True)
    icon_type = Column(String, nullable=True)
    type = Column(String, nullable=False)
    visibility = Column(String, default="private")

    # 类型特定配置快照（针对 agent/workflow 等统一存放）
    config = Column(JSON, default=dict)

    # 便于查询的索引字段（例如 agent 的默认模型）
    default_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True, index=True)

    # 发布信息
    published_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    published_at = Column(DateTime, default=datetime.datetime.now)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # 关系: 指定外键，避免与 App.current_release_id 引起歧义
    app = relationship("App", back_populates="releases", foreign_keys=[app_id])
    
    # 发布人关系 - 使用 primaryjoin 明确指定关联条件
    publisher = relationship(
        "User",
        primaryjoin="AppRelease.published_by == User.id",
        foreign_keys=[published_by],
        lazy="joined",
        viewonly=True  # 只读关系，不会尝试更新
    )
    
    @property
    def publisher_name(self) -> str:
        """发布人名称"""
        if self.publisher:
            return self.publisher.username or self.publisher.email or "未知用户"
        return "未知用户"
    
    def __repr__(self):
        return f"<AppRelease(id={self.id}, app_id={self.app_id}, version={self.version})>"