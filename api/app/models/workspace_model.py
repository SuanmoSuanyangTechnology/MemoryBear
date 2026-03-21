import datetime
from enum import StrEnum
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
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
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    is_active = Column(Boolean, default=True)
    
    # Relationships
    tenant = relationship("Tenants", back_populates="owned_workspaces")  # belongs to tenant
    members = relationship("WorkspaceMember", back_populates="workspace")  # users collaborate through membership
    api_keys = relationship("ApiKey", back_populates="workspace", cascade="all, delete-orphan")  # API Keys
    memory_increments = relationship("MemoryIncrement", back_populates="workspace")

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
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    
    # Relationships
    workspace = relationship("Workspace")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
