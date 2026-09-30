"""Storage and database lifecycle for MinerU-derived image assets."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ...bootstrap import get_settings
from ...models.owned import FILE_ROLE_DERIVED_IMAGE, Document, File
from ...models.owned.file import (
    ASSET_WRITE_BUSY_STATES,
    ASSET_WRITE_CLEANING,
    ASSET_WRITE_CLEANUP_REQUIRED,
    ASSET_WRITE_READY,
    ASSET_WRITE_UPLOADING,
)
from ...services.knowledge_file_storage import KnowledgeFileStorage, generate_kb_file_key

if TYPE_CHECKING:
    from ...runtime import ProcessRuntime

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredMinerUImageAsset:
    file_id: uuid.UUID
    download_url: str


def _reserve_image_upload(
    runtime: ProcessRuntime,
    *,
    document_id: uuid.UUID,
    file_id: uuid.UUID,
    file_ext: str,
    file_name: str,
    file_size: int,
) -> str | None:
    """Register IO under the same document row lock used to start deletion."""
    with runtime.database.sync_session() as session:
        try:
            document = session.get(Document, document_id, with_for_update=True)
            if document is None or document.deletion_started_at is not None:
                return None
            record = session.get(File, file_id, with_for_update=True)
            if record is not None and (
                record.asset_write_state in ASSET_WRITE_BUSY_STATES
                or record.source_document_id != document_id
                or record.kb_id != document.kb_id
                or record.file_role != FILE_ROLE_DERIVED_IMAGE
            ):
                return None
            # Reuse the registered key even if the provider changes the image
            # extension on reparse; overwriting it would lose cleanup evidence.
            file_key = (
                record.file_key
                if record is not None and record.file_key
                else generate_kb_file_key(document.kb_id, file_id, file_ext)
            )
            if record is None:
                record = File(id=file_id)
                session.add(record)
            record.kb_id = document.kb_id
            record.created_by = document.created_by
            record.parent_id = None
            record.file_key = file_key
            record.file_name = file_name
            record.file_ext = file_ext
            record.file_size = file_size
            record.file_role = FILE_ROLE_DERIVED_IMAGE
            record.source_document_id = document_id
            record.asset_write_state = ASSET_WRITE_UPLOADING
            session.commit()
            return file_key
        except Exception:
            session.rollback()
            raise


def _complete_image_io(
    runtime: ProcessRuntime, document_id: uuid.UUID, file_id: uuid.UUID, *, uploaded: bool
) -> bool:
    """Release upload occupancy only after the actual storage coroutine completes."""
    with runtime.database.sync_session() as session:
        try:
            document = session.get(Document, document_id, with_for_update=True)
            record = session.get(File, file_id, with_for_update=True)
            if record is None or record.asset_write_state != ASSET_WRITE_UPLOADING:
                raise RuntimeError("Derived image upload reservation is absent")
            publish = uploaded and document is not None and document.deletion_started_at is None
            record.asset_write_state = (
                ASSET_WRITE_READY if publish else ASSET_WRITE_CLEANUP_REQUIRED
            )
            session.commit()
            return publish
        except Exception:
            session.rollback()
            raise


async def _upload_reserved_image(
    runtime: ProcessRuntime,
    storage: KnowledgeFileStorage,
    *,
    document_id: uuid.UUID,
    file_id: uuid.UUID,
    file_key: str,
    image: Any,
) -> bool:
    # Run completion in the bridge coroutine: a worker soft timeout can interrupt
    # its synchronous caller while the storage operation is still running.
    try:
        await storage.upload(file_key, image.binary, image.content_type)
    except Exception:
        await asyncio.to_thread(_complete_image_io, runtime, document_id, file_id, uploaded=False)
        raise
    return await asyncio.to_thread(_complete_image_io, runtime, document_id, file_id, uploaded=True)


def store_mineru_v3_image(
    runtime: ProcessRuntime,
    *,
    mineru_image,
    tenant_id: Any,
    workspace_id: Any = None,
    document_id: Any,
    source_file_id: Any = None,
    source_file_name: str | None = None,
    source_src: str,
) -> StoredMinerUImageAsset | None:
    tenant_uuid = _parse_uuid(tenant_id)
    workspace_uuid = _parse_uuid(workspace_id)
    document_uuid = _parse_uuid(document_id)
    source_file_uuid = _parse_uuid(source_file_id)
    if tenant_uuid is None or document_uuid is None or not source_src:
        LOGGER.warning("MinerU image storage skipped because required context is missing")
        return None
    file_id = _stable_image_file_id(
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        document_id=document_uuid,
        source_file_id=source_file_uuid,
        source_src=source_src,
    )
    file_ext = _normalize_file_ext(mineru_image.file_ext)
    file_key = _reserve_image_upload(
        runtime,
        document_id=document_uuid,
        file_id=file_id,
        file_ext=file_ext,
        file_name=_build_file_name(source_file_name, mineru_image.name or source_src, file_ext),
        file_size=len(mineru_image.binary),
    )
    if file_key is None:
        LOGGER.info("MinerU image upload skipped: document deleted, deleting, or asset busy")
        return None
    storage = KnowledgeFileStorage(runtime.storage)
    publish = runtime.run_async(
        lambda: _upload_reserved_image(
            runtime,
            storage,
            document_id=document_uuid,
            file_id=file_id,
            file_key=file_key,
            image=mineru_image,
        )
    )
    if not publish:
        return None
    return StoredMinerUImageAsset(file_id=file_id, download_url=_build_image_download_url(file_id))


def _reserve_stale_image_cleanup(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    retained: set[uuid.UUID],
) -> list[tuple[uuid.UUID, str | None]]:
    with runtime.database.sync_session() as session:
        try:
            document = session.get(Document, document_id, with_for_update=True)
            if document is None or document.deletion_started_at is not None:
                return []
            records = (
                session.execute(
                    select(File)
                    .where(
                        File.source_document_id == document_id,
                        File.kb_id == document.kb_id,
                        File.file_role == FILE_ROLE_DERIVED_IMAGE,
                    )
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            candidates = []
            for record in records:
                if record.id in retained or record.asset_write_state in ASSET_WRITE_BUSY_STATES:
                    continue
                record.asset_write_state = ASSET_WRITE_CLEANING
                candidates.append((record.id, record.file_key))
            session.commit()
            return candidates
        except Exception:
            session.rollback()
            raise


def _complete_stale_image_cleanup(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    deleted: bool,
) -> None:
    with runtime.database.sync_session() as session:
        try:
            session.get(Document, document_id, with_for_update=True)
            record = session.get(File, file_id, with_for_update=True)
            if record is None or record.asset_write_state != ASSET_WRITE_CLEANING:
                raise RuntimeError("Derived image cleanup reservation is absent")
            if deleted:
                session.delete(record)
            else:
                record.asset_write_state = ASSET_WRITE_CLEANUP_REQUIRED
            session.commit()
        except Exception:
            session.rollback()
            raise


async def _delete_reserved_image(
    runtime: ProcessRuntime,
    storage: KnowledgeFileStorage,
    document_id: uuid.UUID,
    file_id: uuid.UUID,
    file_key: str | None,
) -> None:
    try:
        if file_key:
            await storage.delete(file_key)
    except Exception:
        await asyncio.to_thread(
            _complete_stale_image_cleanup, runtime, document_id, file_id, deleted=False
        )
        raise
    await asyncio.to_thread(
        _complete_stale_image_cleanup, runtime, document_id, file_id, deleted=True
    )


def cleanup_mineru_v3_images(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    retained_file_ids: set[uuid.UUID] | None = None,
) -> int:
    candidates = _reserve_stale_image_cleanup(runtime, document_id, retained_file_ids or set())
    storage = KnowledgeFileStorage(runtime.storage)
    deleted_count = 0
    for file_id, file_key in candidates:
        try:
            runtime.run_async(
                lambda fid=file_id, key=file_key: _delete_reserved_image(
                    runtime,
                    storage,
                    document_id,
                    fid,
                    key,
                )
            )
            deleted_count += 1
        except Exception as exc:  # noqa: BLE001 - retain failed cleanup evidence.
            LOGGER.warning(
                "MinerU derived image deletion failed file_id=%s error_type=%s",
                file_id,
                type(exc).__name__,
            )
    return deleted_count


def _build_image_download_url(file_id: uuid.UUID) -> str:
    prefix = get_settings().file_local_server_url.rstrip("/")
    path = f"/files/{file_id}"
    return f"{prefix}{path}" if prefix else path


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _stable_image_file_id(
    *,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    document_id: uuid.UUID,
    source_file_id: uuid.UUID | None,
    source_src: str,
) -> uuid.UUID:
    seed = "|".join(
        [
            str(tenant_id),
            str(workspace_id or ""),
            str(document_id),
            str(source_file_id or ""),
            source_src,
        ]
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, f"memorybear:rag:mineru-v3:image:{seed}")


def _normalize_file_ext(file_ext: str | None) -> str:
    normalized = (file_ext or ".png").strip().lower()
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return ".jpg" if normalized == ".jpe" else normalized


def _build_file_name(source_file_name: str | None, image_name: str, file_ext: str) -> str:
    source_stem = Path(source_file_name or "document").stem or "document"
    image_stem = Path(image_name).stem or "image"
    stem = f"{source_stem}-{image_stem}"
    return f"{stem[: max(1, 255 - len(file_ext))]}{file_ext}"


__all__ = [
    "StoredMinerUImageAsset",
    "cleanup_mineru_v3_images",
    "store_mineru_v3_image",
]
