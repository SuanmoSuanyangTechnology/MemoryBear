"""app_releases 只读映射（模型影响面分析用）。"""

from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.dialects.postgresql import JSON, UUID

from ..base import ReadOnlyBase


class AppRelease(ReadOnlyBase):
    __tablename__ = "app_releases"

    id = Column(UUID(as_uuid=True), primary_key=True)
    app_id = Column(UUID(as_uuid=True), nullable=False)
    version = Column(Integer, nullable=False)
    version_name = Column(String, nullable=False)
    config = Column(JSON, default=dict)
    default_model_config_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
