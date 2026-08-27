import uuid
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.utils.datetime_utils import utcnow_naive
from app.db import Base


class WorkspaceRole(StrEnum):
    manager = "manager"
    member = "member"


class InviteStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked" 
    expired = "expired"


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "retention_days >= 0 AND retention_days <= 3650",
            name="ck_workspaces_retention_days_range",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String, index=True, nullable=False)
    icon = Column(String, nullable=True)
    iconType = Column(String, nullable=True)
    description = Column(String, nullable=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)  # belongs to tenant
    storage_type = Column(String, nullable=True)
    llm = Column(String, nullable=True)
    embedding = Column(String, nullable=True)
    rerank = Column(String, nullable=True)
    vision = Column(String, nullable=True)
    audio = Column(String, nullable=True)
    video = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    is_default_config = Column(Boolean, default=False, server_default="false", nullable=False)
    default_model_notice_pending = Column(Boolean, default=False, server_default="false", nullable=False)
    memory_config = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Boolean, default=True)
    retention_days = Column(Integer, nullable=False, default=0, server_default="0")

    # Relationships
    tenant = relationship("Tenants", back_populates="owned_workspaces")  # belongs to tenant
    members = relationship("WorkspaceMember", back_populates="workspace")  # users collaborate through membership
    api_keys = relationship("ApiKey", back_populates="workspace", cascade="all, delete-orphan")  # API Keys
    memory_increments = relationship("MemoryIncrement", back_populates="workspace")
    end_users = relationship("EndUser", back_populates="workspace", cascade="all, delete-orphan")


class WorkspaceDefaultModelPreset(Base):
    __tablename__ = "workspace_default_model_presets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    singleton_key = Column(String, nullable=False, unique=True, default="default")
    llm_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False)
    embedding_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False)
    rerank_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False)
    vision_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False)
    audio_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False)
    video_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    role = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    user = relationship("User", back_populates="workspaces")
    workspace = relationship("Workspace", back_populates="members")


class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    email = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)  # WorkspaceRole: manager or member
    token_hash = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, nullable=False, default=InviteStatus.pending)  # InviteStatus
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    # Relationships
    workspace = relationship("Workspace")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
