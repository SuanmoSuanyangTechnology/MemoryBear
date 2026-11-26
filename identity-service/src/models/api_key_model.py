import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase, SharedBase, utcnow_naive


class ApiKeyType(StrEnum):
    AGENT = "agent"
    CLUSTER = "multi_agent"
    WORKFLOW = "workflow"
    PURE_WORKFLOW = "pure_workflow"
    SERVICE = "service"


class ApiKey(SharedBase):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    api_key = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)
    scopes = Column(JSONB, default=list)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    resource_id = Column(UUID(as_uuid=True))
    rate_limit = Column(Integer, default=10)
    daily_request_limit = Column(Integer, default=10000)
    rate_limit_disabled = Column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    quota_limit = Column(Integer)
    quota_used = Column(Integer, default=0)
    usage_count = Column(Integer, default=0)
    last_used_at = Column(DateTime)
    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = Column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class ApiKeyLog(ReadOnlyBase):
    __tablename__ = "api_key_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id = Column(UUID(as_uuid=True), nullable=False)
    endpoint = Column(String(255), nullable=False)
    method = Column(String(10), nullable=False)
    ip_address = Column(String(50))
    user_agent = Column(Text)
    status_code = Column(Integer)
    response_time = Column(Integer)
    tokens_used = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
