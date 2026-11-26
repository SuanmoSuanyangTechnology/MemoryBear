"""Read-only mirror of ``apps``.

Only the three columns the API-key management plane reads: resource existence,
the API-key-type/app-type match gate, and the publish gate.
"""
import uuid
from enum import StrEnum

from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class AppType(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"
    PURE_WORKFLOW = "pure_workflow"
    MULTI_AGENT = "multi_agent"


class App(ReadOnlyBase):
    __tablename__ = "apps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(String, nullable=False)
    current_release_id = Column(UUID(as_uuid=True), nullable=True)
