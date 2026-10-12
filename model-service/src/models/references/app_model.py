"""apps 只读映射（模型影响面分析用）。"""

from enum import StrEnum

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from ..base import ReadOnlyBase


class AppStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class App(ReadOnlyBase):
    __tablename__ = "apps"

    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String, nullable=False)
    status = Column(String, default="draft")
    current_release_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Boolean, default=True)
