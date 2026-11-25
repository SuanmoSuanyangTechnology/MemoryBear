"""Minimal read projections for dashboard counters owned by the API host."""

from sqlalchemy import Boolean, Column, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class DashboardApp(ReadOnlyBase):
    __tablename__ = "apps"
    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=True)


class DashboardAppShare(ReadOnlyBase):
    __tablename__ = "app_shares"
    id = Column(UUID(as_uuid=True), primary_key=True)
    source_app_id = Column(UUID(as_uuid=True), nullable=False)
    target_workspace_id = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=True)


class DashboardKnowledge(ReadOnlyBase):
    __tablename__ = "knowledges"
    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    parent_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=True)


class DashboardApiKey(ReadOnlyBase):
    __tablename__ = "api_keys"
    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)


class DashboardApiKeyLog(ReadOnlyBase):
    __tablename__ = "api_key_logs"
    id = Column(UUID(as_uuid=True), primary_key=True)
    api_key_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, nullable=False)
