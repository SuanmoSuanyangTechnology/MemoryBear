"""Read-only mapping for the API-owned ``ontology_scene_type`` table."""

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class OntologySceneType(ReadOnlyBase):
    __tablename__ = "ontology_scene_type"

    scene_type_id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    scene_name = Column(String, nullable=False)
    is_system_default = Column(Boolean, nullable=False)
