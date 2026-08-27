"""Best-effort complete-image visual enhancement."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import as_completed
from dataclasses import dataclass

from ....runtime import get_worker_runtime
from ..context import ImageVisionScope, LogicalChunk, LogicalChunkType
from ..prompts import vision_llm_figure_describe_prompt
from ..vision import vision_llm_chunk

LOGGER = logging.getLogger(__name__)


@dataclass
class ImageVisionEnhancementStats:
    image_count: int
    ready_count: int
    success_count: int = 0
    empty_count: int = 0
    failed_count: int = 0
    elapsed_seconds: float = 0.0


def enhance_complete_image_chunks_with_vision(
    chunks: Sequence[LogicalChunk],
    vision_model,
    enabled_scopes: set[ImageVisionScope],
    callback: Callable | None = None,
    log_prefix: str = "Chunk",
    lang: str = "Chinese",
    progress_start: float = 0.80,
    progress_span: float = 0.03,
) -> ImageVisionEnhancementStats:
    eligible = [
        chunk
        for chunk in chunks
        if chunk.type is LogicalChunkType.IMAGE
        and chunk.image_tag_complete
        and chunk.source_image_key is not None
        and chunk.image_vision_scope in enabled_scopes
    ]
    groups: dict[int, list[LogicalChunk]] = {}
    for chunk in eligible:
        groups.setdefault(chunk.source_image_key, []).append(chunk)

    pending: list[tuple[int, list[LogicalChunk], LogicalChunk]] = []
    ready_count = 0
    for source_key, group in groups.items():
        existing = next(
            (
                str(chunk.metadata.get("vision_text") or "").strip()
                for chunk in group
                if str(chunk.metadata.get("vision_text") or "").strip()
            ),
            "",
        )
        if existing:
            for chunk in group:
                chunk.metadata["vision_text"] = existing
            ready_count += 1
            continue
        representative = next((chunk for chunk in group if chunk.image is not None), None)
        if representative is not None:
            ready_count += 1
            pending.append((source_key, group, representative))

    stats = ImageVisionEnhancementStats(image_count=len(eligible), ready_count=ready_count)
    if not vision_model:
        if eligible:
            LOGGER.warning("%s image vision skipped because no model is available", log_prefix)
        return stats
    if not pending:
        return stats

    started_at = time.monotonic()
    prompt = vision_llm_figure_describe_prompt(getattr(vision_model, "lang", lang))
    executor = get_worker_runtime().vision_executor
    total = len(pending)
    futures = {}
    completed_count = 0
    for index, (source_key, group, representative) in enumerate(pending, start=1):
        submitted_at = time.monotonic()
        try:
            future = executor.submit(
                vision_llm_chunk,
                representative.image,
                vision_model,
                prompt,
                callback,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one image submission.
            stats.failed_count += 1
            completed_count += 1
            LOGGER.warning(
                "%s image vision submission failed index=%s total=%s source_key=%s "
                "error_type=%s",
                log_prefix,
                index,
                total,
                source_key,
                type(exc).__name__,
            )
            _callback(
                callback,
                progress_start + progress_span * completed_count / total,
                f"{log_prefix} image vision failed: {completed_count}/{total}.",
            )
            continue
        futures[future] = (index, source_key, group, submitted_at)

    for future in as_completed(futures):
        index, source_key, group, submitted_at = futures[future]
        completed_count += 1
        progress = progress_start + progress_span * completed_count / total
        try:
            vision_text = str(future.result() or "").strip()
        except Exception as exc:  # noqa: BLE001 - isolate one image failure.
            stats.failed_count += 1
            LOGGER.warning(
                "%s image vision failed index=%s total=%s source_key=%s error_type=%s",
                log_prefix,
                index,
                total,
                source_key,
                type(exc).__name__,
            )
            _callback(
                callback,
                progress,
                f"{log_prefix} image vision failed: {completed_count}/{total}.",
            )
            continue
        if vision_text:
            for chunk in group:
                chunk.metadata["vision_text"] = vision_text
            stats.success_count += 1
        else:
            stats.empty_count += 1
        LOGGER.info(
            "%s image vision finished index=%s total=%s source_key=%s elapsed=%.2fs chars=%s",
            log_prefix,
            index,
            total,
            source_key,
            time.monotonic() - submitted_at,
            len(vision_text),
        )
        _callback(
            callback,
            progress,
            f"{log_prefix} image vision finished: {completed_count}/{total}.",
        )

    stats.elapsed_seconds = time.monotonic() - started_at
    return stats


def _callback(callback: Callable | None, progress: float, message: str) -> None:
    if callback:
        callback(progress, message)


__all__ = ["ImageVisionEnhancementStats", "enhance_complete_image_chunks_with_vision"]
