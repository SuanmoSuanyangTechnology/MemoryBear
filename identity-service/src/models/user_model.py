"""只读映射 core users 表；表结构归 core 管理，core 改列名/删列时须同步本文件。"""
from sqlalchemy import Boolean, Column, DateTime
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class User(ReadOnlyBase):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    is_superuser = Column(Boolean, nullable=False)
    is_active = Column(Boolean, nullable=False)
    current_workspace_id = Column(UUID(as_uuid=True), nullable=True)
    updated_at = Column(DateTime, nullable=True)
