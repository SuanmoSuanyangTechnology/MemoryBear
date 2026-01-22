"""
File metadata model for storing file storage information.

This model stores metadata about files uploaded to the storage backend,
including the storage key, content type, and other relevant information.
"""

import datetime
import uuid

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class FileMetadata(Base):
    """
    Model for storing file metadata.

    Attributes:
        id: Primary key UUID.
        tenant_id: The tenant that owns the file.
        workspace_id: The workspace the file belongs to.
        file_key: The unique storage key for the file.
        file_name: Original file name.
        file_ext: File extension (e.g., .pdf, .md).
        file_size: File size in bytes.
        content_type: MIME type of the file.
        status: Upload status (pending, completed, failed).
        created_at: Timestamp when the file was uploaded.
    """

    __tablename__ = "file_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="Tenant ID")
    workspace_id = Column(UUID(as_uuid=True), nullable=False, index=True, comment="Workspace ID")
    file_key = Column(String(512), nullable=False, unique=True, index=True, comment="Storage file key")
    file_name = Column(String(255), nullable=False, comment="Original file name")
    file_ext = Column(String(32), nullable=False, comment="File extension")
    file_size = Column(Integer, nullable=False, default=0, comment="File size in bytes")
    content_type = Column(String(128), nullable=True, comment="MIME content type")
    status = Column(String(16), nullable=False, default="pending", comment="Upload status: pending, completed, failed")
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now)
