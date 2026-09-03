"""Storage and database lifecycle for MinerU-derived image assets."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from ...bootstrap import get_settings
from ...models.owned import FILE_ROLE_DERIVED_IMAGE, Document, File
from ...services.knowledge_file_storage import KnowledgeFileStorage, generate_kb_file_key

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredMinerUImageAsset:
    file_id: uuid.UUID
    download_url: str


@dataclass(frozen=True)
class _DocumentFileContext:
    kb_id: uuid.UUID
    created_by: uuid.UUID


def store_mineru_v3_image(
    runtime,
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

    with runtime.database.sync_session() as session:
        document = session.get(Document, document_uuid)
        context = (
            _DocumentFileContext(kb_id=document.kb_id, created_by=document.created_by)
            if document is not None
            else None
        )
    if context is None:
        LOGGER.warning("MinerU image storage skipped because the source document is absent")
        return None

    file_id = _stable_image_file_id(
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        document_id=document_uuid,
        source_file_id=source_file_uuid,
        source_src=source_src,
    )
    file_ext = _normalize_file_ext(mineru_image.file_ext)
    file_name = _build_file_name(
        source_file_name,
        mineru_image.name or source_src,
        file_ext,
    )
    file_key = generate_kb_file_key(context.kb_id, file_id, file_ext)
    storage = KnowledgeFileStorage(runtime.storage)
    runtime.run_async(
        lambda: storage.upload(
            file_key,
            mineru_image.binary,
            mineru_image.content_type,
        )
    )

    with runtime.database.sync_session() as session:
        record = session.get(File, file_id)
        if record is None:
            record = File(id=file_id)
            session.add(record)
        record.kb_id = context.kb_id
        record.created_by = context.created_by
        record.parent_id = None
        record.file_key = file_key
        record.file_name = file_name
        record.file_ext = file_ext
        record.file_size = len(mineru_image.binary)
        record.file_role = FILE_ROLE_DERIVED_IMAGE
        record.source_document_id = document_uuid
        session.commit()
    return StoredMinerUImageAsset(
        file_id=file_id,
        download_url=_build_image_download_url(file_id),
    )


def cleanup_mineru_v3_images(
    runtime,
    document_id: uuid.UUID,
    retained_file_ids: set[uuid.UUID] | None = None,
) -> int:
    retained = retained_file_ids or set()
    with runtime.database.sync_session() as session:
        records = session.execute(
            select(File.id, File.file_key).where(
                File.source_document_id == document_id,
                File.file_role == FILE_ROLE_DERIVED_IMAGE,
            )
        ).all()
        candidates = [
            (file_id, file_key)
            for file_id, file_key in records
            if file_id not in retained
        ]
    if not candidates:
        return 0

    storage = KnowledgeFileStorage(runtime.storage)
    deleted_ids: list[uuid.UUID] = []
    for file_id, file_key in candidates:
        try:
            if file_key:
                runtime.run_async(lambda key=file_key: storage.delete(key))
            deleted_ids.append(file_id)
        except Exception as exc:  # noqa: BLE001 - one asset must not block the remaining cleanup.
            LOGGER.warning(
                "MinerU derived image deletion failed file_id=%s error_type=%s",
                file_id,
                type(exc).__name__,
            )
    if not deleted_ids:
        return 0
    with runtime.database.sync_session() as session:
        session.execute(
            delete(File).where(
                File.id.in_(deleted_ids),
                File.source_document_id == document_id,
                File.file_role == FILE_ROLE_DERIVED_IMAGE,
            )
        )
        session.commit()
    return len(deleted_ids)


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
