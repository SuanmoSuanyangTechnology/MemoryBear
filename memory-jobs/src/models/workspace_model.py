"""Host-owned workspace table."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String, nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    storage_type = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=True)
    is_default_config = Column(Boolean, nullable=True)
    memory_config = Column(UUID(as_uuid=True), nullable=True)
    #: 「模型选择」六列由宿主维护；生成任务只读 ``llm``（默认配置空间改用 preset）。
    llm = Column(String, nullable=True)
    embedding = Column(String, nullable=True)
    rerank = Column(String, nullable=True)
    vision = Column(String, nullable=True)
    audio = Column(String, nullable=True)
    video = Column(String, nullable=True)
