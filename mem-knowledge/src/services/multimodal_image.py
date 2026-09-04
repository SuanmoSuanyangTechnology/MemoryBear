"""Safe request-local image validation and preparation."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import uuid
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PIL import Image, ImageOps, UnidentifiedImageError
from redbear_model import ImageEmbeddingContent
from sqlalchemy import select

from ..errors import KnowledgeError
from ..models.owned import File
from .knowledge_file_storage import KnowledgeFileStorage

if TYPE_CHECKING:
    from ..runtime import ProcessRuntime

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BASE64_CHARACTERS = ((MAX_IMAGE_BYTES + 2) // 3) * 4
_MEDIA_TYPE_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/bmp": "BMP",
}
_FORMAT_TO_MEDIA_TYPE = {value: key for key, value in _MEDIA_TYPE_TO_FORMAT.items()}
_STORAGE_TRANSCODE_FORMATS = frozenset({"GIF"})
_STORAGE_IMAGE_MAX_EDGE = 4096
_STORAGE_JPEG_QUALITY = 85
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidatedImageData:
    media_type: str
    data_uri: str = field(repr=False)
    decoded_bytes: int
    width: int
    height: int


@dataclass(frozen=True)
class FileAssetSnapshot:
    file_id: uuid.UUID
    file_key: str


def _validation_error(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_VALIDATION_ERROR", message)


def _input_limit_error(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_MULTIMODAL_INPUT_LIMIT", message)


def validate_image_data_uri(content: str) -> ValidatedImageData:
    header, separator, payload = content.partition(",")
    if not separator or not header.startswith("data:") or not header.endswith(";base64"):
        raise _validation_error("Image query must be a Base64 data URI")
    media_type = header[5:-7]
    expected_format = _MEDIA_TYPE_TO_FORMAT.get(media_type)
    if expected_format is None:
        raise _validation_error("Image query media type is not supported")
    if not payload:
        raise _validation_error("Image query content must not be empty")
    if len(payload) > MAX_BASE64_CHARACTERS:
        raise _input_limit_error("Image query exceeds the 10 MiB limit")
    try:
        binary = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _validation_error("Image query Base64 content is invalid") from exc
    if not binary:
        raise _validation_error("Image query content must not be empty")
    if len(binary) > MAX_IMAGE_BYTES:
        raise _input_limit_error("Image query exceeds the 10 MiB limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(binary)) as image:
                width, height = image.size
                actual_format = image.format
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _input_limit_error("Image query dimensions exceed the safe limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise _validation_error("Image query content is not a valid image") from exc
    if width < 1 or height < 1:
        raise _validation_error("Image query dimensions must be positive")
    if actual_format != expected_format:
        raise _validation_error("Image query media type does not match its content")
    return ValidatedImageData(
        media_type=media_type,
        data_uri=content,
        decoded_bytes=len(binary),
        width=width,
        height=height,
    )


def _open_storage_image(content: bytes) -> tuple[str, int, int]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as image:
            image_format = str(image.format or "")
            width, height = image.size
            image.verify()
    if (
        image_format not in _FORMAT_TO_MEDIA_TYPE
        and image_format not in _STORAGE_TRANSCODE_FORMATS
    ) or width < 1 or height < 1:
        raise ValueError("unsupported storage image")
    return image_format, width, height


def _compress_storage_image(content: bytes) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as source:
            source.seek(0)
            source.load()
            image = ImageOps.exif_transpose(source)
            if max(image.size) > _STORAGE_IMAGE_MAX_EDGE:
                image.thumbnail(
                    (_STORAGE_IMAGE_MAX_EDGE, _STORAGE_IMAGE_MAX_EDGE),
                    Image.Resampling.LANCZOS,
                )
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert("RGB")
            target = io.BytesIO()
            image.save(target, format="JPEG", quality=_STORAGE_JPEG_QUALITY)
            return target.getvalue()


def prepare_storage_image(
    content: bytes,
    *,
    phase: str,
) -> ImageEmbeddingContent | None:
    if phase not in {"index", "rerank"}:
        raise ValueError("storage image phase must be index or rerank")
    try:
        image_format, width, height = _open_storage_image(content)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        logger.warning(
            "event=multimodal_image_unavailable phase=%s error_type=%s",
            phase,
            type(exc).__name__,
        )
        return None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        logger.warning(
            "event=multimodal_image_unavailable phase=%s error_type=%s",
            phase,
            type(exc).__name__,
        )
        return None

    prepared = content
    requires_transcode = image_format in _STORAGE_TRANSCODE_FORMATS
    media_type = _FORMAT_TO_MEDIA_TYPE.get(image_format, "image/jpeg")
    exceeds_size_limit = len(content) > MAX_IMAGE_BYTES
    if exceeds_size_limit:
        logger.warning(
            "event=multimodal_image_compression_started phase=%s "
            "original_bytes=%s limit_bytes=%s original_width=%s original_height=%s",
            phase,
            len(content),
            MAX_IMAGE_BYTES,
            width,
            height,
        )
    if exceeds_size_limit or requires_transcode:
        try:
            prepared = _compress_storage_image(content)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            logger.warning(
                "event=multimodal_image_unavailable phase=%s error_type=%s",
                phase,
                type(exc).__name__,
            )
            return None
        except (OSError, SyntaxError, ValueError) as exc:
            logger.warning(
                "event=multimodal_image_unavailable phase=%s error_type=%s",
                phase,
                type(exc).__name__,
            )
            return None
        media_type = "image/jpeg"
        if len(prepared) > MAX_IMAGE_BYTES:
            logger.warning(
                "event=multimodal_image_compression_limit_exceeded phase=%s "
                "original_bytes=%s compressed_bytes=%s limit_bytes=%s "
                "original_width=%s original_height=%s",
                phase,
                len(content),
                len(prepared),
                MAX_IMAGE_BYTES,
                width,
                height,
            )
            return None
    encoded = base64.b64encode(prepared).decode("ascii")
    return ImageEmbeddingContent(
        media_type=media_type,
        data_uri=f"data:{media_type};base64,{encoded}",
        decoded_bytes=len(prepared),
    )


class StorageImageResolver:
    def __init__(self, runtime: ProcessRuntime, knowledge_id: uuid.UUID) -> None:
        self._runtime = runtime
        self._knowledge_id = knowledge_id

    def __call__(
        self,
        asset_ids: Sequence[str],
        *,
        phase: str,
    ) -> dict[str, ImageEmbeddingContent]:
        normalized_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for value in asset_ids:
            try:
                file_id = uuid.UUID(str(value))
            except (TypeError, ValueError):
                continue
            if file_id not in seen:
                seen.add(file_id)
                normalized_ids.append(file_id)
        if not normalized_ids:
            return {}
        with self._runtime.database.sync_session() as session:
            rows = session.execute(
                select(File.id, File.file_key).where(
                    File.id.in_(normalized_ids),
                    File.kb_id == self._knowledge_id,
                )
            ).all()
            snapshots_by_id = {
                file_id: FileAssetSnapshot(file_id=file_id, file_key=file_key)
                for file_id, file_key in rows
                if file_key
            }
        snapshots = [
            snapshots_by_id[file_id]
            for file_id in normalized_ids
            if file_id in snapshots_by_id
        ]
        downloaded = self._runtime.run_async(
            lambda: self._download_all(snapshots)
        )
        result: dict[str, ImageEmbeddingContent] = {}
        for snapshot, content in zip(snapshots, downloaded, strict=True):
            if isinstance(content, BaseException):
                logger.warning(
                    "event=multimodal_image_unavailable phase=%s error_type=%s",
                    phase,
                    type(content).__name__,
                )
                continue
            prepared = prepare_storage_image(content, phase=phase)
            if prepared is not None:
                result[str(snapshot.file_id)] = prepared
        return result

    async def _download_all(
        self,
        snapshots: Sequence[FileAssetSnapshot],
    ) -> list[bytes | BaseException]:
        storage = KnowledgeFileStorage(self._runtime.storage)
        return list(
            await asyncio.gather(
                *(storage.download(snapshot.file_key) for snapshot in snapshots),
                return_exceptions=True,
            )
        )


async def resolve_storage_images_async(
    runtime: ProcessRuntime,
    knowledge_id: uuid.UUID,
    asset_ids: Sequence[str],
    *,
    phase: str,
) -> dict[str, ImageEmbeddingContent]:
    normalized_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in asset_ids:
        try:
            file_id = uuid.UUID(str(value))
        except (TypeError, ValueError):
            continue
        if file_id not in seen:
            seen.add(file_id)
            normalized_ids.append(file_id)
    if not normalized_ids:
        return {}
    async with runtime.database.async_session() as session:
        rows = (
            await session.execute(
                select(File.id, File.file_key).where(
                    File.id.in_(normalized_ids),
                    File.kb_id == knowledge_id,
                )
            )
        ).all()
        snapshots_by_id = {
            file_id: FileAssetSnapshot(file_id=file_id, file_key=file_key)
            for file_id, file_key in rows
            if file_key
        }
    snapshots = [
        snapshots_by_id[file_id]
        for file_id in normalized_ids
        if file_id in snapshots_by_id
    ]
    storage = KnowledgeFileStorage(runtime.storage)
    downloaded = await asyncio.gather(
        *(storage.download(snapshot.file_key) for snapshot in snapshots),
        return_exceptions=True,
    )
    result: dict[str, ImageEmbeddingContent] = {}
    for snapshot, content in zip(snapshots, downloaded, strict=True):
        if isinstance(content, BaseException):
            logger.warning(
                "event=multimodal_image_unavailable phase=%s error_type=%s",
                phase,
                type(content).__name__,
            )
            continue
        prepared = await asyncio.to_thread(prepare_storage_image, content, phase=phase)
        if prepared is not None:
            result[str(snapshot.file_id)] = prepared
    return result


__all__ = [
    "MAX_IMAGE_BYTES",
    "StorageImageResolver",
    "resolve_storage_images_async",
    "ValidatedImageData",
    "prepare_storage_image",
    "validate_image_data_uri",
]
