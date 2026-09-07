"""只读映射 core api_keys 表；表结构归 core 管理，core 改列名/删列时须同步本文件。"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class ApiKey(ReadOnlyBase):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True)
    api_key = Column(String(255), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(Boolean, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    scopes = Column(JSONB, nullable=True)
    rate_limit = Column(Integer, nullable=True)
    daily_request_limit = Column(Integer, nullable=True)
    rate_limit_disabled = Column(Boolean, nullable=False, default=False)
    created_by = Column(UUID(as_uuid=True), nullable=True)  # key 创建者用户（快照 user_id 来源）
    updated_at = Column(DateTime, nullable=True)
