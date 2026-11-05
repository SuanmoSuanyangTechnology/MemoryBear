"""Read-only mapping for the API-owned ``ontology_scene`` table."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class OntologySceneRow(ReadOnlyBase):
    __tablename__ = "ontology_scene"

    scene_id = Column(UUID(as_uuid=True), primary_key=True)
    scene_name = Column(String(200), nullable=False)
    scene_description = Column(Text, nullable=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
