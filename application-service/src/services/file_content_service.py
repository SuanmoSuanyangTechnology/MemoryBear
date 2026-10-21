"""Shared file content read capability (business side).

In private deployments, user-uploaded files are only reachable in the intranet object storage,
so external/public models cannot pull the content back by URL; meanwhile local files
(transfer_method=local_file) keep only file_id / upload_file_id in message metadata, variable
pools and workflow FileObject, with an empty url string. Therefore any entry point that hands
attachment content to downstream (multimodal LLM egress, knowledge base image retrieval,
document extraction, tool calls) must fetch the bytes from the storage backend itself via
file_id, and must not assume URL reachability.

This module centralizes three things so every consumer does not reimplement them:

1. resolve -- locate the file from file_id / this service's permanent URL and validate
   workspace/tenant ownership;
2. read    -- read bytes straight from the storage backend (no HTTP, no URL reachability
   dependency);
3. encode  -- encode per the downstream contract (image normalization + base64 data URI).

Convention: a URL on this platform is only a handle for display / internal byte fetching, not
an egress contract; anything leaving this service boundary (sent to a model or a knowledge
base) always travels as bytes / data URI.
"""

from __future__ import annotations

import base64
import io
import logging
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..config import current_settings
from ..errors import BizCode
from ..errors import BusinessException
from ..db import get_async_db_context
from ..models.file_metadata_model import FileMetadata
from .file_storage_service import FileStorageService

logger = logging.getLogger(__name__)

# Path marker of permanent download URLs minted by this service (see the permanent route in
# file_storage_controller).
_PERMANENT_PATH_MARKER = "/storage/permanent/"

# Image byte limit accepted by downstream (the MemoryBear knowledge base), kept in sync with
# its validation: exceeding the limit fails immediately, no point reading a large file only to
# be rejected.
IMAGE_MAX_BYTES = 10 * 1024 * 1024

# Downstream accepts only these media types and cross-checks them against the real image
# format, so the media type must be derived from the actual format, never from the declared
# value.
_PIL_FORMAT_TO_MEDIA = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}


@dataclass(frozen=True, slots=True)
class FileReference:
    """Normalized view of an attachment reference, compatible with dict / FileObject / FileInput
    payloads.

    Field sources are mapped for each payload shape so consumers no longer do their own
    getattr/get:
    - type: ``type`` (may carry a MIME suffix such as image/png) -> falls back to
      ``origin_file_type``;
    - file identity: ``file_id`` (FileObject) -> falls back to ``upload_file_id`` (FileInput /
      frontend format);
    - address: ``url`` (present only for remote_url; an empty string for local files);
    - transfer method: ``transfer_method``, distinguishing the two byte-fetch paths, "local
      file" and "remote address".
    """

    file_type: str | None = None
    url: str | None = None
    file_id: str | None = None
    name: str | None = None
    transfer_method: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> "FileReference | None":
        """Normalize any attachment payload into a reference; returns None when unrecognizable."""
        if payload is None or isinstance(payload, (str, bytes, int, float, bool)):
            return None

        if isinstance(payload, dict):
            def _field(key: str) -> Any:
                return payload.get(key)
        else:
            def _field(key: str) -> Any:
                return getattr(payload, key, None)

        raw_type = _field("type") or _field("origin_file_type")
        file_type = None
        if raw_type is not None:
            # type may be a FileType enum; take its literal value
            file_type = str(getattr(raw_type, "value", raw_type)).strip() or None

        raw_url = _field("url")
        url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None

        raw_file_id = _field("file_id") or _field("upload_file_id")
        file_id = str(raw_file_id).strip() if raw_file_id else None

        raw_name = _field("name")
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else None

        raw_method = _field("transfer_method")
        transfer_method = None
        if raw_method is not None:
            transfer_method = str(getattr(raw_method, "value", raw_method)).strip().lower() or None

        if file_type is None and url is None and file_id is None:
            return None
        return cls(
            file_type=file_type,
            url=url,
            file_id=file_id,
            name=name,
            transfer_method=transfer_method,
        )

    @property
    def is_image(self) -> bool:
        return bool(self.file_type) and self.file_type.startswith("image")

    @property
    def is_local(self) -> bool:
        """Explicitly declared local upload (transfer_method=local_file)."""
        return self.transfer_method == "local_file"

    @property
    def locator(self) -> str | None:
        """Stable short identifier for logs / audit / cache keys; contains no base64 or long URL."""
        if self.url:
            return self.url
        if self.file_id:
            return f"file_id:{self.file_id}"
        return None


@dataclass(frozen=True, slots=True)
class FileContent:
    """Result of a single attachment read."""

    data: bytes
    file_id: str | None = None
    file_name: str | None = None
    content_type: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)


def build_permanent_file_url(file_id: uuid.UUID | str) -> str:
    """Permanent download URL minted by this service (internal handle only; never sent to models)."""
    return f"{current_settings().file_local_server_url.rstrip('/')}{_PERMANENT_PATH_MARKER}{file_id}"


def extract_permanent_file_id(url: Any, *, trust_any_host: bool = False) -> str | None:
    """Recognize a permanent download URL minted by this service and extract the file ID from it.

    trust_any_host=False (URL comes from a request; untrusted): only the current
    FILE_LOCAL_SERVER_URL prefix is accepted, so an arbitrary public URL cannot be mistaken for
    a file owned by this service and trigger a server-side fetch (SSRF).
    trust_any_host=True (URL comes from persisted metadata / variable pools; trusted): the path
    shape is additionally accepted, so after a domain or port change permanent URLs in old data
    can still be restored to a file ID for byte fetching.
    """
    if not isinstance(url, str):
        return None
    normalized_url = url.split("?", 1)[0]
    configured_prefix = f"{current_settings().file_local_server_url.rstrip('/')}{_PERMANENT_PATH_MARKER}"
    if normalized_url.startswith(configured_prefix):
        candidate = normalized_url[len(configured_prefix):]
    elif trust_any_host:
        path = urlparse(normalized_url).path
        marker_index = path.rfind(_PERMANENT_PATH_MARKER)
        if marker_index < 0:
            return None
        candidate = path[marker_index + len(_PERMANENT_PATH_MARKER):]
    else:
        return None

    candidate = candidate.strip("/")
    try:
        return str(uuid.UUID(candidate))
    except (TypeError, ValueError):
        return None


def encode_image_data_uri(data: bytes) -> str | None:
    """Normalize image bytes into the base64 data URI required by downstream; returns None for
    non-images or oversized data.

    Matches the image encoding criteria of the knowledge base integration layer: GIF is
    flattened to its first frame and converted to JPEG; only JPEG/PNG/WEBP/BMP are allowed,
    within IMAGE_MAX_BYTES.
    """
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            actual_format = (image.format or "").upper()

            if actual_format == "GIF":
                # Downstream does not support GIF: flatten the first frame onto white and
                # convert to JPEG
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                buffer = io.BytesIO()
                flattened.save(buffer, format="JPEG", quality=90)
                data = buffer.getvalue()
                actual_format = "JPEG"

            media_type = _PIL_FORMAT_TO_MEDIA.get(actual_format)
    except (OSError, SyntaxError, ValueError) as exc:
        logger.info("file_content_image_decode_failed error=%s", type(exc).__name__)
        return None

    if media_type is None or len(data) > IMAGE_MAX_BYTES:
        logger.info(
            "file_content_image_rejected media=%s bytes=%s",
            media_type,
            len(data) if data else 0,
        )
        return None

    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _as_uuid(value: Any) -> uuid.UUID | None:
    """Normalize workspace_id / tenant_id into a UUID; returns None when unparsable."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        logger.debug("file_content_scope_id_unparsable value=%r", value)
        return None


@dataclass(frozen=True, slots=True)
class _FileMetadataSnapshot:
    """Metadata snapshot safe to use after the session closes (avoids detached ORM instances)."""

    file_id: str
    file_key: str
    file_name: str | None
    file_size: int
    content_type: str | None


async def _load_file_metadata(
    session: AsyncSession | Session,
    file_id: str,
    workspace_id: Any,
    tenant_id: Any,
) -> _FileMetadataSnapshot | None:
    """Query file metadata within its ownership scope to prevent cross-tenant reads.

    The tenant is a hard isolation boundary, so filtering by tenant_id takes precedence; the
    workspace dimension is not tightened further -- in shared-app scenarios the uploader's
    workspace may differ from the app's workspace while the file is still legitimate data for
    the same tenant, and tightening would make it unqueryable. Only when no tenant is available
    does the lookup fall back to the workspace dimension. When neither is available the read is
    refused (returns None and the caller reports an explicit error).
    """
    try:
        normalized_file_id = uuid.UUID(str(file_id))
    except (TypeError, ValueError):
        return None

    scoped_tenant_id = _as_uuid(tenant_id)
    scoped_workspace_id = _as_uuid(workspace_id)
    if scoped_tenant_id is not None:
        scope_condition = FileMetadata.tenant_id == scoped_tenant_id
    elif scoped_workspace_id is not None:
        scope_condition = FileMetadata.workspace_id == scoped_workspace_id
    else:
        return None

    conditions = [
        FileMetadata.id == normalized_file_id,
        FileMetadata.status == "completed",
        scope_condition,
    ]
    if isinstance(session, AsyncSession):
        metadata = (await session.execute(select(FileMetadata).where(*conditions))).scalar_one_or_none()
    else:
        metadata = session.query(FileMetadata).filter(*conditions).first()

    if metadata is None:
        return None
    return _FileMetadataSnapshot(
        file_id=str(metadata.id),
        file_key=metadata.file_key,
        file_name=metadata.file_name,
        file_size=int(metadata.file_size or 0),
        content_type=metadata.content_type,
    )


async def _read_by_file_id(
    reference: FileReference,
    *,
    workspace_id: Any,
    tenant_id: Any,
    db: AsyncSession | Session | None,
    limit: int,
) -> FileContent:
    """Local file: metadata lookup by ownership scope + direct storage read (no HTTP egress)."""
    if workspace_id is None and tenant_id is None:
        # Same criteria as multimodal egress: without ownership context, local files cannot
        # be read
        raise BusinessException(
            "缺少工作空间或租户上下文，无法读取本地文件",
            BizCode.FILE_NOT_FOUND,
        )

    if db is not None:
        metadata = await _load_file_metadata(db, reference.file_id, workspace_id, tenant_id)
    else:
        async with get_async_db_context() as session:
            metadata = await _load_file_metadata(session, reference.file_id, workspace_id, tenant_id)

    if metadata is None:
        # Return unavailable uniformly to callers, so other tenants' file existence is not
        # leaked
        raise BusinessException(
            "文件不存在、未完成或无权访问",
            BizCode.FILE_NOT_FOUND,
        )

    declared_size = metadata.file_size
    if declared_size > limit:
        raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)

    content = await FileStorageService().download_file(metadata.file_key)
    if len(content) > limit:
        raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)
    if declared_size and len(content) != declared_size:
        logger.error(
            "本地文件大小与元数据不一致",
            extra={
                "file_id": metadata.file_id,
                "expected_size": declared_size,
                "actual_size": len(content),
            },
        )
        raise BusinessException("文件读取失败", BizCode.FILE_READ_ERROR)

    return FileContent(
        data=content,
        file_id=metadata.file_id,
        file_name=metadata.file_name,
        content_type=metadata.content_type,
    )


async def _read_by_permanent_url(reference: FileReference, *, limit: int) -> FileContent:
    """Legacy-data fallback: only permanent URLs minted by this service; restored to file_id and read locally."""
    file_id = extract_permanent_file_id(reference.url, trust_any_host=True)
    if file_id is None:
        raise BusinessException(
            "附件 URL 不是本服务可读取的文件地址",
            BizCode.FILE_READ_ERROR,
        )

    # Rebuild the request against this service's own address from the restored file_id, so an
    # external URL's host is never trusted (SSRF)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(build_permanent_file_url(file_id), follow_redirects=True)
            response.raise_for_status()
            content = response.content
    except Exception as exc:
        logger.error(f"读取本服务附件 URL 失败: {exc}", exc_info=True)
        raise BusinessException("文件读取失败", BizCode.FILE_READ_ERROR)

    if len(content) > limit:
        raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)

    return FileContent(
        data=content,
        file_id=file_id,
        content_type=response.headers.get("content-type"),
    )


async def read_file_content(
    payload: FileReference | Any,
    *,
    workspace_id: Any = None,
    tenant_id: Any = None,
    db: AsyncSession | Session | None = None,
    max_bytes: int | None = None,
) -> FileContent:
    """Read attachment content; file_id-based direct storage read takes priority, then this
    service's permanent URL.

    Args:
        payload: FileReference / dict (FileObject) / FileInput are all accepted.
        workspace_id: Owning workspace; used as the scope-check fallback when tenant_id is
            unavailable.
        tenant_id: Owning tenant, preferred (hard isolation boundary).
            At least one of the two must be given, otherwise local files are refused.
        db: Existing session; when omitted, a short internal session is opened (closed right
            after use, does not join the caller's transaction).
        max_bytes: Per-file limit, defaulting to current_settings().max_file_size.

    Raises:
        BusinessException: reference unusable, access denied, size limit exceeded, or storage
            read failure.
    """
    reference = payload if isinstance(payload, FileReference) else FileReference.from_payload(payload)
    if reference is None:
        raise BusinessException("附件引用无效，无法读取文件内容", BizCode.FILE_READ_ERROR)

    limit = int(max_bytes) if max_bytes else int(current_settings().max_file_size)

    # Local files (empty url, or explicitly marked local_file) read storage directly by
    # file_id first; a remote address goes through the local read path only when it can be
    # restored to a file_id minted by this service -- otherwise it is genuinely left to the
    # caller to handle by URL, since querying the DB with an arbitrary remote file_id is futile.
    if reference.file_id and (reference.is_local or not reference.url):
        return await _read_by_file_id(
            reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            limit=limit,
        )
    if reference.url:
        return await _read_by_permanent_url(reference, limit=limit)
    if reference.file_id:
        return await _read_by_file_id(
            reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            limit=limit,
        )

    raise BusinessException(
        "附件缺少文件标识（file_id / URL），无法读取内容",
        BizCode.FILE_READ_ERROR,
    )


async def try_read_file_content(
    payload: FileReference | Any,
    *,
    workspace_id: Any = None,
    tenant_id: Any = None,
    db: AsyncSession | Session | None = None,
    max_bytes: int | None = None,
) -> FileContent | None:
    """Log and return None on read failure, for callers where one attachment must not affect others."""
    reference = payload if isinstance(payload, FileReference) else FileReference.from_payload(payload)
    try:
        return await read_file_content(
            reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            max_bytes=max_bytes,
        )
    except BusinessException as exc:
        logger.warning(
            "附件内容读取失败，已跳过: %s locator=%s",
            exc,
            reference.locator if reference else None,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - catch-all; a single attachment must not break the round
        logger.warning(
            "附件内容读取异常，已跳过: %s locator=%s",
            exc,
            reference.locator if reference else None,
            exc_info=True,
        )
        return None


async def resolve_image_retrieval_query(
    payload: FileReference | Any,
    *,
    workspace_id: Any = None,
    tenant_id: Any = None,
    db: AsyncSession | Session | None = None,
) -> Any | None:
    """Convert an image reference into the image retrieval query required by the knowledge base
    (data URI form); returns None on failure.

    - Local reference (transfer_method=local_file, or empty url with only a file_id): read the
      storage bytes directly and encode locally, so private deployments do not depend on URL
      reachability;
    - Remote URL: read directly as well when it can be restored to a file_id minted by this
      service; otherwise reuse the knowledge base integration layer's download + validation
      implementation, keeping both sides consistent.

    The return value is ``app.schemas.chunk_schema.ImageRetrievalQuery`` (imported lazily here
    to avoid coupling the import order of services and schemas).
    """
    from ..integrations.knowledge.retrieval_policy import build_image_retrieval_query
    from ..schemas.chunk_schema import ImageRetrievalQuery

    reference = payload if isinstance(payload, FileReference) else FileReference.from_payload(payload)
    if reference is None:
        return None

    # Decide whether to fetch bytes locally: local files qualify directly; a remote address
    # qualifies only when it can be restored to a permanent URL minted by this service and
    # converted to a file_id for a direct read (in private deployments this path does not
    # depend on URL reachability).
    direct_reference: FileReference | None = None
    if reference.is_local or not reference.url:
        direct_reference = reference
    else:
        permanent_file_id = extract_permanent_file_id(reference.url, trust_any_host=True)
        if permanent_file_id:
            direct_reference = FileReference(
                file_type=reference.file_type,
                file_id=permanent_file_id,
                name=reference.name,
                transfer_method="local_file",
            )

    if direct_reference is not None:
        content = await try_read_file_content(
            direct_reference,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            db=db,
            max_bytes=IMAGE_MAX_BYTES,
        )
        if content is not None:
            data_uri = encode_image_data_uri(content.data)
            if data_uri is None:
                logger.warning(
                    "图片内容无法编码为检索用 data URI locator=%s bytes=%s",
                    direct_reference.locator,
                    content.size,
                )
            else:
                logger.info(
                    "图片检索 query 已由本地字节编码 locator=%s bytes=%s",
                    direct_reference.locator,
                    content.size,
                )
                return ImageRetrievalQuery(modality="image", content=data_uri)
        # If the local read failed and a URL is still present, fall through to the URL path
        # below as a fallback

    if reference.url:
        return await build_image_retrieval_query(reference.url)

    return None
