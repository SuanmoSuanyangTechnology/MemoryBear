"""Read-only mappings used to resolve LLM configuration for generation.

These tables stay owned by the API migration chain; the generation path only
reads them. The one write this service performs (the generated insight/summary
text on ``end_users``) goes through ``EndUser`` in ``end_user_model``.
"""

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class WorkspaceDefaultModelPreset(ReadOnlyBase):
    __tablename__ = "workspace_default_model_presets"

    id = Column(UUID(as_uuid=True), primary_key=True)
    singleton_key = Column(String, nullable=False)
    llm_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    embedding_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    rerank_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    vision_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    audio_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    video_model_config_id = Column(UUID(as_uuid=True), nullable=False)


class ModelConfig(ReadOnlyBase):
    __tablename__ = "model_configs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False)
    is_public = Column(Boolean, nullable=False)


class EndUserInfo(ReadOnlyBase):
    __tablename__ = "end_user_info"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    meta_data = Column(JSONB, nullable=True)
    updated_at = Column(DateTime, nullable=True)
