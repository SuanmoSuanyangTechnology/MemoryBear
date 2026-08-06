import asyncio
import logging
import threading
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.rag.chunk.parser.mineru_v3_client import MinerUV3Image
from app.db import get_db_context
from app.models.document_model import Document
from app.models.file_model import FILE_ROLE_DERIVED_IMAGE, File
from app.services.file_storage_service import FileStorageService, generate_kb_file_key

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
    *,
    mineru_image: MinerUV3Image,
    tenant_id: Any,
    workspace_id: Any = None,
    document_id: Any,
    source_file_id: Any = None,
    source_file_name: str | None = None,
    source_src: str,
    storage_service: FileStorageService | None = None,
) -> StoredMinerUImageAsset | None:
    tenant_uuid = _parse_uuid(tenant_id)
    workspace_uuid = _parse_uuid(workspace_id)
    document_uuid = _parse_uuid(document_id)
    source_file_uuid = _parse_uuid(source_file_id)
    if tenant_uuid is None or document_uuid is None or not source_src:
        LOGGER.warning(
            "[MinerUV3] image storage skipped: missing context tenant_id=%s document_id=%s src=%s",
            tenant_id,
            document_id,
            source_src,
        )
        return None

    file_id = _stable_image_file_id(
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        document_id=document_uuid,
        source_file_id=source_file_uuid,
        source_src=source_src,
    )
    document_context = _load_document_file_context(document_uuid)
    if document_context is None:
        LOGGER.warning("[MinerUV3] image storage skipped: document not found document_id=%s", document_uuid)
        return None

    file_ext = _normalize_file_ext(mineru_image.file_ext)
    file_name = _build_file_name(source_file_name, mineru_image.name or source_src, file_ext)
    file_key = generate_kb_file_key(document_context.kb_id, file_id, file_ext)
    storage_service = storage_service or FileStorageService()

    _run_async(
        lambda: storage_service.storage.upload(
            file_key=file_key,
            content=mineru_image.binary,
            content_type=mineru_image.content_type,
        )
    )
    _upsert_derived_image_file(
        file_id=file_id,
        kb_id=document_context.kb_id,
        created_by=document_context.created_by,
        source_document_id=document_uuid,
        file_key=file_key,
        file_name=file_name,
        file_ext=file_ext,
        file_size=len(mineru_image.binary),
        file_role=FILE_ROLE_DERIVED_IMAGE,
    )
    return StoredMinerUImageAsset(file_id=file_id, download_url=_build_image_download_url(file_id))


def _build_image_download_url(file_id: uuid.UUID) -> str:
    server_url = (settings.FILE_LOCAL_SERVER_URL or "").rstrip("/")
    path = f"/files/{file_id}"
    if not server_url:
        return path
    return f"{server_url}{path}"


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
    if normalized == ".jpe":
        normalized = ".jpg"
    return normalized or ".png"


def _build_file_name(source_file_name: str | None, image_name: str, file_ext: str) -> str:
    document_stem = Path(source_file_name or "document").stem or "document"
    image_stem = Path(image_name).stem or "image"
    stem = f"{document_stem}-{image_stem}"
    max_stem_length = max(1, 255 - len(file_ext))
    return f"{stem[:max_stem_length]}{file_ext}"


def _load_document_file_context(document_id: uuid.UUID) -> _DocumentFileContext | None:
    with get_db_context() as db:
        document = db.get(Document, document_id)
        if document is None:
            return None
        return _DocumentFileContext(kb_id=document.kb_id, created_by=document.created_by)


def _upsert_derived_image_file(
    *,
    file_id: uuid.UUID,
    kb_id: uuid.UUID,
    created_by: uuid.UUID,
    source_document_id: uuid.UUID,
    file_key: str,
    file_name: str,
    file_ext: str,
    file_size: int,
    file_role: str,
) -> None:
    with get_db_context() as db:
        record = db.get(File, file_id)
        if record is None:
            record = File(
                id=file_id,
                kb_id=kb_id,
                created_by=created_by,
                parent_id=None,
                file_key=file_key,
                file_name=file_name,
                file_ext=file_ext,
                file_size=file_size,
                file_role=file_role,
                source_document_id=source_document_id,
            )
            db.add(record)
        else:
            record.kb_id = kb_id
            record.created_by = created_by
            record.parent_id = None
            record.file_key = file_key
            record.file_name = file_name
            record.file_ext = file_ext
            record.file_size = file_size
            record.file_role = file_role
            record.source_document_id = source_document_id
        db.commit()


def cleanup_mineru_v3_images(
    document_id: uuid.UUID,
    retained_file_ids: set[uuid.UUID] | None = None,
    storage_service: FileStorageService | None = None,
) -> int:
    retained_file_ids = retained_file_ids or set()
    with get_db_context() as db:
        query = db.query(File).filter(
            File.source_document_id == document_id,
            File.file_role == FILE_ROLE_DERIVED_IMAGE,
        )
        candidates = [
            (record.id, record.file_key)
            for record in query.all()
            if record.id not in retained_file_ids
        ]

    if not candidates:
        return 0

    storage_service = storage_service or FileStorageService()
    deleted_ids: list[uuid.UUID] = []
    for file_id, file_key in candidates:
        try:
            if file_key:
                _run_async(lambda file_key=file_key: storage_service.delete_file(file_key))
            deleted_ids.append(file_id)
        except Exception:
            LOGGER.warning(
                "[MinerUV3] failed to delete derived image asset: document_id=%s file_id=%s",
                document_id,
                file_id,
                exc_info=True,
            )

    if not deleted_ids:
        return 0

    with get_db_context() as db:
        db.query(File).filter(
            File.id.in_(deleted_ids),
            File.source_document_id == document_id,
            File.file_role == FILE_ROLE_DERIVED_IMAGE,
        ).delete(synchronize_session=False)
        db.commit()
    return len(deleted_ids)


def _run_async(coro_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")
