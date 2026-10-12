"""workspaces / workspace_default_model_presets 只读映射（模型影响面分析用）。

注意槽位语义差异：workspaces 六列是**字符串模型名**，presets 六列是 model_configs UUID。
"""

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from ..base import ReadOnlyBase


class Workspace(ReadOnlyBase):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String, nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    llm = Column(String, nullable=True)
    embedding = Column(String, nullable=True)
    rerank = Column(String, nullable=True)
    vision = Column(String, nullable=True)
    audio = Column(String, nullable=True)
    video = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)


class WorkspaceDefaultModelPreset(ReadOnlyBase):
    __tablename__ = "workspace_default_model_presets"

    id = Column(UUID(as_uuid=True), primary_key=True)
    llm_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    embedding_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    rerank_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    vision_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    audio_model_config_id = Column(UUID(as_uuid=True), nullable=False)
    video_model_config_id = Column(UUID(as_uuid=True), nullable=False)
