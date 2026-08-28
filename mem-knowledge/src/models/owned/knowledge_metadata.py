"""Knowledge metadata models copied from the legacy API service."""

import uuid

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from ...db import KnowledgeBase
from ...utils.datetime_utils import utcnow_naive


class KnowledgeMetadata(KnowledgeBase):
    __tablename__ = "knowledge_metadatas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, comment="tenant id")
    knowledge_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="knowledge id",
    )
    type = Column(String, nullable=False, comment="field type: string | number | time")
    name = Column(String(255), nullable=False, comment="field name")
    created_by = Column(UUID(as_uuid=True), comment="creator")
    updated_by = Column(UUID(as_uuid=True), comment="updater")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    __table_args__ = (
        UniqueConstraint(
            "knowledge_id",
            "name",
            name="uq_knowledge_metadata_name",
        ),
    )


class KnowledgeMetadataBinding(KnowledgeBase):
    __tablename__ = "knowledge_metadata_bindings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, comment="tenant id")
    knowledge_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="knowledge id",
    )
    metadata_id = Column(UUID(as_uuid=True), nullable=False, comment="metadata id")
    document_id = Column(UUID(as_uuid=True), nullable=False, comment="document id")
    created_by = Column(UUID(as_uuid=True), comment="creator")
    created_at = Column(DateTime, default=utcnow_naive)

    __table_args__ = (
        UniqueConstraint(
            "knowledge_id",
            "metadata_id",
            "document_id",
            name="uq_knowledge_metadata_binding",
        ),
    )
