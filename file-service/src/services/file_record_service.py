"""Async data access for ``file_metadata`` records.

Every method opens its own short-lived ``AsyncSession`` through
``get_async_db_context`` and closes it before returning, so no pooled
connection is held while a ``FileResponse``/``StreamingResponse`` streams its
body (the monolith's explicit ``db.close()`` before ``FileResponse`` is the
same concern, solved here structurally).

``get`` returns a detached instance. ``expire_on_commit=False`` keeps its
loaded attributes usable after the session closes; callers only read them.

``delete`` takes a file id rather than a detached instance: re-loading inside
the session avoids SQLAlchemy's detached-instance errors and keeps the
ownership check the caller performed as close as possible to the write.

``create_pending`` / ``complete`` / ``fail`` back the internal lifecycle routes
used by callers that stream bytes to the storage backend themselves (TTS,
sandbox artifacts). They generate the file id and key up front so the caller
and the service agree on the object key before any bytes are written.
"""

from __future__ import annotations

import os
import uuid

from ..db import get_async_db_context
from ..errors import BizCode, BusinessException
from ..models.file_metadata_model import FileMetadata
from .file_storage_service import generate_file_key

_MAX_FILE_EXT_LENGTH = 32


def _normalise_file_ext(file_name: str, file_ext: str | None) -> str:
    """Return a lowercase dotted extension, deriving it from the name when absent."""
    candidate = (file_ext or "").strip().lower()
    if candidate:
        if not candidate.startswith("."):
            candidate = f".{candidate}"
    else:
        candidate = os.path.splitext(file_name)[1].lower()
    if candidate == ".":
        return ""
    if len(candidate) > _MAX_FILE_EXT_LENGTH:
        raise BusinessException("Invalid file extension", BizCode.BAD_REQUEST)
    return candidate


class FileRecordService:
    """Short-session reads/writes for file metadata."""

    async def get(self, file_id: uuid.UUID) -> FileMetadata | None:
        async with get_async_db_context() as session:
            return await session.get(FileMetadata, file_id)

    async def delete(self, file_id: uuid.UUID) -> bool:
        """Delete the record by id; returns False when it is already gone."""
        async with get_async_db_context() as session:
            record = await session.get(FileMetadata, file_id)
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True

    async def create_pending(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        file_name: str,
        file_ext: str | None = None,
        content_type: str | None = None,
        file_size: int | None = None,
    ) -> FileMetadata:
        """Register a pending record; the caller then writes the bytes itself."""
        ext = _normalise_file_ext(file_name, file_ext)
        file_id = uuid.uuid4()
        record = FileMetadata(
            id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_key=generate_file_key(tenant_id, workspace_id, file_id, ext),
            file_name=file_name,
            file_ext=ext,
            file_size=file_size or 0,
            content_type=content_type,
            status="pending",
        )
        async with get_async_db_context() as session:
            session.add(record)
            await session.commit()
        return record

    async def complete(
        self, file_id: uuid.UUID, file_size: int
    ) -> FileMetadata | None:
        """Mark the record completed with the written size; None when missing."""
        async with get_async_db_context() as session:
            record = await session.get(FileMetadata, file_id)
            if record is None:
                return None
            record.file_size = file_size
            record.status = "completed"
            await session.commit()
            return record

    async def fail(self, file_id: uuid.UUID) -> FileMetadata | None:
        """Mark the record failed; None when missing."""
        async with get_async_db_context() as session:
            record = await session.get(FileMetadata, file_id)
            if record is None:
                return None
            record.status = "failed"
            await session.commit()
            return record
