"""Capability checks and query construction for image knowledge retrieval."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Any

import httpx
from PIL import Image

from app.schemas.chunk_schema import ImageRetrievalQuery, RetrieveType

from .contracts import KnowledgeCallContext
from .retriever import KnowledgeRetriever

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
# mem-knowledge only accepts these media types and cross-checks them with PIL,
# so the declared media type is derived from the real image format.
_PIL_FORMAT_TO_MEDIA = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}


def _image_query_is_compatible(
    retrieve_type: RetrieveType,
    *,
    rerank_mode: Any = None,
    enable_graph_retrieval: int = 0,
) -> bool:
    """Reject combinations the knowledge service documents as text-only."""
    if retrieve_type is not RetrieveType.HYBRID:
        return True
    if str(rerank_mode) == "weighted_score":
        return False
    return enable_graph_retrieval != 1


async def image_retrieval_supported(
    retriever: KnowledgeRetriever,
    *,
    kb_ids: list[str],
    retrieve_type: RetrieveType,
    context: KnowledgeCallContext,
    rerank_id: str | None = None,
    rerank_mode: Any = None,
    enable_graph_retrieval: int = 0,
) -> bool:
    """Return whether this request may use an image query.

    Capability lookup is deliberately fail-closed so a temporarily unavailable
    or older knowledge service preserves the existing text-only behavior.
    """
    if not kb_ids or not _image_query_is_compatible(
        retrieve_type,
        rerank_mode=rerank_mode,
        enable_graph_retrieval=enable_graph_retrieval,
    ):
        return False

    try:
        policy = await retriever.retrieval_policy(
            kb_ids=kb_ids,
            context=context,
            rerank_id=rerank_id,
        )
    except Exception as exc:
        logger.info(
            "knowledge_image_policy_unavailable source=%s error=%s",
            context.source.value,
            type(exc).__name__,
        )
        return False

    return "image" in policy.get(retrieve_type.value, frozenset())


def _encode_image_data_uri(raw: bytes) -> tuple[str, str] | None:
    """Normalize image bytes into ``(media_type, data URI)`` accepted downstream."""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            actual_format = (image.format or "").upper()

            if actual_format == "GIF":
                # GIF is unsupported downstream; flatten the first frame to JPEG.
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                buffer = io.BytesIO()
                flattened.save(buffer, format="JPEG", quality=90)
                raw = buffer.getvalue()
                actual_format = "JPEG"

            media_type = _PIL_FORMAT_TO_MEDIA.get(actual_format)
    except (OSError, SyntaxError, ValueError) as exc:
        logger.info("knowledge_image_decode_failed error=%s", type(exc).__name__)
        return None

    if media_type is None or not raw or len(raw) > _MAX_IMAGE_BYTES:
        logger.info(
            "knowledge_image_rejected media=%s bytes=%s",
            media_type,
            len(raw) if raw else 0,
        )
        return None

    encoded = base64.b64encode(raw).decode("ascii")
    return media_type, f"data:{media_type};base64,{encoded}"


async def build_image_retrieval_query(
    url: str,
    *,
    timeout: float = 15.0,
    retries: int = 2,
) -> ImageRetrievalQuery | None:
    """Download ``url`` and return a validated image query, or ``None`` on failure.

    mem-knowledge rejects plain URLs for image queries; it requires a Base64
    data URI whose media type matches the actual image bytes.

    The download is retried because the image is commonly reached through a
    best-effort tunnel whose connections are occasionally reset on the first
    attempt; the retries are transparent and only kick in on transport errors.
    """
    last_exc: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                raw = response.content
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.info(
                "knowledge_image_download_failed attempt=%s/%s error=%s",
                attempt + 1,
                retries + 1,
                type(exc).__name__,
            )
            if attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
    else:
        logger.warning(
            "knowledge_image_download_gave_up url=%s error=%s",
            url[:200],
            type(last_exc).__name__ if last_exc else "Unknown",
        )
        return None

    encoded = _encode_image_data_uri(raw)
    if encoded is None:
        logger.warning("knowledge_image_encode_failed url=%s bytes=%s", url[:200], len(raw))
        return None
    media_type, data_uri = encoded
    logger.info(
        "knowledge_image_encoded url=%s media=%s bytes=%s",
        url[:200],
        media_type,
        len(raw),
    )
    return ImageRetrievalQuery(modality="image", content=data_uri)
