import datetime
from enum import StrEnum
from re import LOCALE
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive

class IconType(StrEnum):
    """Icon type enum"""
    LOCALE = "locale"
    REMOTE = "remote"

# Visibility: private | workspace | public
class AppVisibility(StrEnum):
    """Visibility enum""" 
    PRIVATE = "private"
    WORKSPACE = "workspace"
    PUBLIC = "public"

# App type: agent | workflow | pure_workflow | multi_agent
class AppType(StrEnum):
    """App type enum"""
    AGENT = "agent"
    WORKFLOW = "workflow"
    PURE_WORKFLOW = "pure_workflow"
    MULTI_AGENT = "multi_agent"


# App status: draft | active | archived
class AppStatus(StrEnum):
    """App status enum"""
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

    # App type: agent | workflow, etc.
    type = Column(String, index=True, nullable=False)

    # Visibility: private | workspace | public
    visibility = Column(String, default="workspace")

    # Status: draft | active | archived
    status = Column(String, default="draft")

    # Tags or extended metadata
    tags = Column(JSON, default=list)

    # Current published release pointer (points to the snapshot after publish, unaffected by edits)
    current_release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("app_releases.id", use_alter=True, name="fk_apps_current_release_id"),
        nullable=True,
        index=True,
    )

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    # One-to-one: agent config (effective only when type=agent)
    agent_config = relationship(
        "AgentConfig",
        back_populates="app",
        uselist=False,
        cascade="all, delete-orphan",
    )
    
    # One-to-one: multi-agent config (effective only when type=multi_agent)
    multi_agent_config = relationship(
        "MultiAgentConfig",
        back_populates="app",
        uselist=False,
        cascade="all, delete-orphan",
    )
    
    # One-to-one: workflow config (effective only when type=workflow)
    workflow_config = relationship(
        "WorkflowConfig",
        back_populates="app",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Release associations
    current_release = relationship("AppRelease", foreign_keys=[current_release_id])
    # Explicit foreign keys to avoid ambiguity with current_release_id
    releases = relationship(
        "AppRelease",
        back_populates="app",
        cascade="all, delete-orphan",
        foreign_keys="AppRelease.app_id",
    )
    
    # Conversation associations
    conversations = relationship(
        "Conversation",
        back_populates="app",
        cascade="all, delete-orphan"
    )

    # Reverse relationship with EndUser
    end_users = relationship(
        "EndUser",
        back_populates="app",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<App(id={self.id}, name={self.name}, type={self.type})>"