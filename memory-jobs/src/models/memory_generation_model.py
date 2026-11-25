"""Host-owned tables the generation tasks read to resolve the LLM.

These stay owned by the API migration chain; the generation tasks only read
them. The one write this service performs (the generated insight/summary text on
``end_users``) goes through ``EndUser`` in ``end_user_model``.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import Base


class MemoryConfigRow(Base):
    __tablename__ = "memory_config"

    config_id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=True)


class WorkspaceDefaultModelPreset(Base):
    __tablename__ = "workspace_default_model_presets"

    id = Column(UUID(as_uuid=True), primary_key=True)
    singleton_key = Column(String, nullable=False)
    llm_model_config_id = Column(UUID(as_uuid=True), nullable=True)
    embedding_model_config_id = Column(UUID(as_uuid=True), nullable=True)
    rerank_model_config_id = Column(UUID(as_uuid=True), nullable=True)
    vision_model_config_id = Column(UUID(as_uuid=True), nullable=True)
    audio_model_config_id = Column(UUID(as_uuid=True), nullable=True)
    video_model_config_id = Column(UUID(as_uuid=True), nullable=True)


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    name = Column(String, nullable=True)
    type = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=True)
    is_public = Column(Boolean, nullable=True)


class EndUserInfo(Base):
    __tablename__ = "end_user_info"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    meta_data = Column(JSONB, nullable=True)
    updated_at = Column(DateTime, nullable=True)
