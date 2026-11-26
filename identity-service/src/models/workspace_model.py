import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import SharedBase, utcnow_naive


class WorkspaceRole(StrEnum):
    manager = "manager"
    member = "member"


class InviteStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"
    expired = "expired"


class Workspace(SharedBase):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    icon = Column(String, nullable=True)
    iconType = Column(String, nullable=True)
    description = Column(String, nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    storage_type = Column(String, nullable=False, default="neo4j")
    llm = Column(String, nullable=True)
    embedding = Column(String, nullable=True)
    rerank = Column(String, nullable=True)
    vision = Column(String, nullable=True)
    audio = Column(String, nullable=True)
    video = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    is_default_config = Column(Boolean, default=False, server_default="false", nullable=False)
    default_model_notice_pending = Column(
        Boolean, default=False, server_default="false", nullable=False
    )
    memory_config = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Boolean, default=True)
    retention_days = Column(Integer, nullable=True)


class WorkspaceDefaultModelPreset(SharedBase):
    __tablename__ = "workspace_default_model_presets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    singleton_key = Column(String, nullable=False, default="default")
    llm_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    embedding_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    rerank_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    vision_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    audio_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    video_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class WorkspaceMember(SharedBase):
    __tablename__ = "workspace_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)


class WorkspaceInvite(SharedBase):
    __tablename__ = "workspace_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    email = Column(String, nullable=False)
    role = Column(String, nullable=False)
    token_hash = Column(String, nullable=False)
    status = Column(String, nullable=False, default=InviteStatus.pending)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
