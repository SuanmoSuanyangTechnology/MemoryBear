"""只读映射 core workspaces/workspace_members 表；表结构归 core 管理，core 改列名/删列时须同步本文件。"""
from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class Workspace(ReadOnlyBase):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(Boolean, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class WorkspaceMember(ReadOnlyBase):
    __tablename__ = "workspace_members"

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=True)
