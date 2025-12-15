import datetime
from enum import StrEnum
from re import LOCALE
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from app.db import Base

class IconType(StrEnum):
    """图标类型枚举"""
    LOCALE = "locale"
    REMOTE = "remote"

# 可见性：private | workspace | public
class AppVisibility(StrEnum):
    """可见性枚举""" 
    PRIVATE = "private"
    WORKSPACE = "workspace"
    PUBLIC = "public"

# 应用类型：agent | workflow | multi_agent
class AppType(StrEnum):
    """应用类型枚举"""
    AGENT = "agent"
    WORKFLOW = "workflow"
    MULTI_AGENT = "multi_agent"


# 应用状态：draft | active | archived
class AppStatus(StrEnum):
    """应用状态枚举"""
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class App(Base):
    __tablename__ = "apps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False, comment="workspaces.id")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")

    name = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    icon = Column(String, nullable=True)
    icon_type = Column(String, nullable=True)

    # 应用类型：agent | workflow 等
    type = Column(String, index=True, nullable=False)

    # 可见性：private | workspace | public
    visibility = Column(String, default="workspace")

    # 状态：draft | active | archived
    status = Column(String, default="draft")

    # 标签或扩展元数据
    tags = Column(JSON, default=list)

    # 当前已发布版本指针（发布后指向快照，不受编辑影响）
    current_release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_releases.id", use_alter=True, name="fk_apps_current_release_id"),
        nullable=True,
        index=True,
    )

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # 一对一：Agent 配置（仅当 type=agent 时有效）
    agent_config = relationship(
        "AgentConfig",
        back_populates="app",
        uselist=False,
        cascade="all, delete-orphan",
    )
    
    # 一对一：多 Agent 配置（仅当 type=multi_agent 时有效）
    multi_agent_config = relationship(
        "MultiAgentConfig",
        back_populates="app",
        uselist=False,
        cascade="all, delete-orphan",
    )
    
    # 一对一：工作流配置（仅当 type=workflow 时有效）
    workflow_config = relationship(
        "WorkflowConfig",
        back_populates="app",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # 发布版本关联
    current_release = relationship("AppRelease", foreign_keys=[current_release_id])
    # 指定外键以避免与 current_release_id 造成歧义
    releases = relationship(
        "AppRelease",
        back_populates="app",
        cascade="all, delete-orphan",
        foreign_keys="AppRelease.app_id",
    )
    
    # 会话关联
    conversations = relationship(
        "Conversation",
        back_populates="app",
        cascade="all, delete-orphan"
    )

    # 与 EndUser 的反向关系
    end_users = relationship(
        "EndUser",
        back_populates="app",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<App(id={self.id}, name={self.name}, type={self.type})>"