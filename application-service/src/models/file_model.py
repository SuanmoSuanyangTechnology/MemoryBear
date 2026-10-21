"""Frozen cross-domain copy of the monolith ``files`` model.

The kb domain owns the table (DDL and writes); this service only reads the
original-file key behind a cited document.
"""

import uuid

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from ..utils.datetime_utils import utcnow_naive
from .cross_domain_base import CrossDomainBase as Base

FILE_ROLE_SOURCE = "source"


class File(Base):
    __tablename__ = "files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    kb_id = Column(UUID(as_uuid=True), nullable=False, comment="knowledges.id")
    created_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    parent_id = Column(UUID(as_uuid=True), nullable=True, default=None, comment="parent folder id")
    file_name = Column(
        String,
        index=True,
        nullable=False,
        comment="file name or folder name,default folder name is /",
    )
    file_ext = Column(String, index=True, nullable=False, comment="file extension:folder|pdf")
    file_size = Column(Integer, default=0, comment="file size(byte)")
    file_url = Column(String, index=True, nullable=True, comment="file comes from a website url")
    file_key = Column(
        String(512),
        nullable=True,
        index=True,
        comment="storage file key for FileStorageService",
    )
    file_role = Column(
        String(32),
        nullable=False,
        default=FILE_ROLE_SOURCE,
        server_default=FILE_ROLE_SOURCE,
        index=True,
        comment="source or derived_image",
    )
    source_document_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="documents.id for a derived image asset",
    )
    created_at = Column(DateTime, default=utcnow_naive)
