import asyncio
import logging
import threading
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.rag.chunk.parser.mineru_v3_client import MinerUV3Image
from app.db import get_db_context
from app.models.file_metadata_model import FileMetadata
from app.services.file_storage_service import FileStorageService, generate_file_key


LOGGER = logging.getLogger(__name__)


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
) -> dict[str, str]:
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
        return {}

    file_id = _stable_image_file_id(
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        document_id=document_uuid,
        source_file_id=source_file_uuid,
        source_src=source_src,
    )
    file_ext = _normalize_file_ext(mineru_image.file_ext)
    file_name = _build_file_name(source_file_name, mineru_image.name or source_src, file_ext)
    file_key = generate_file_key(tenant_uuid, workspace_uuid, file_id, file_ext)
    storage_service = storage_service or FileStorageService()

    _upsert_file_metadata(
        file_id=file_id,
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        file_key=file_key,
        file_name=file_name,
        file_ext=file_ext,
        file_size=len(mineru_image.binary),
        content_type=mineru_image.content_type,
        status="pending",
    )
    try:
        uploaded_key = _run_async(
            lambda: storage_service.upload_file(
                tenant_id=tenant_uuid,
                workspace_id=workspace_uuid,
                file_id=file_id,
                file_ext=file_ext,
                content=mineru_image.binary,
                content_type=mineru_image.content_type,
            )
        )
    except Exception:
        _mark_file_metadata_failed(file_id)
        raise

    _upsert_file_metadata(
        file_id=file_id,
        tenant_id=tenant_uuid,
        workspace_id=workspace_uuid,
        file_key=uploaded_key,
        file_name=file_name,
        file_ext=file_ext,
        file_size=len(mineru_image.binary),
        content_type=mineru_image.content_type,
        status="completed",
    )
    return {
        "image_file_id": str(file_id),
        "image_download_url": _build_image_download_url(file_id),
    }


def _build_image_download_url(file_id: uuid.UUID) -> str:
    path = f"/api/storage/permanent/{file_id}"
    base_url = (settings.BASE_URL or "").rstrip("/")
    if not base_url:
        return path
    if base_url.endswith("/api") and path.startswith("/api/"):
        path = path[len("/api"):]
    return f"{base_url}{path}"


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


def _upsert_file_metadata(
    *,
    file_id: uuid.UUID,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    file_key: str,
    file_name: str,
    file_ext: str,
    file_size: int,
    content_type: str | None,
    status: str,
) -> None:
    with get_db_context() as db:
        record = db.get(FileMetadata, file_id)
        if record is None:
            record = FileMetadata(
                id=file_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                file_key=file_key,
                file_name=file_name,
                file_ext=file_ext,
                file_size=file_size,
                content_type=content_type,
                status=status,
            )
            db.add(record)
        else:
            record.tenant_id = tenant_id
            record.workspace_id = workspace_id
            record.file_key = file_key
            record.file_name = file_name
            record.file_ext = file_ext
            record.file_size = file_size
            record.content_type = content_type
            record.status = status
        db.commit()


def _mark_file_metadata_failed(file_id: uuid.UUID) -> None:
    try:
        with get_db_context() as db:
            record = db.get(FileMetadata, file_id)
            if record is not None:
                record.status = "failed"
                db.commit()
    except Exception:
        LOGGER.warning("[MinerUV3] failed to mark image metadata as failed: file_id=%s", file_id, exc_info=True)


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
