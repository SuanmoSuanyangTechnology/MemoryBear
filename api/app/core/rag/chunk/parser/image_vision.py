import logging
import time
from concurrent.futures import as_completed
from dataclasses import dataclass
from typing import Callable, Sequence

from app.core.rag.app.picture import vision_llm_chunk
from app.core.rag.chunk.context import ParsedBlock, ParsedBlockType
from app.core.rag.deepdoc.parser import figure_parser
from app.core.rag.prompts.generator import vision_llm_figure_describe_prompt


LOGGER = logging.getLogger(__name__)


@dataclass
class ImageVisionEnhancementStats:
    image_count: int
    ready_count: int
    success_count: int = 0
    empty_count: int = 0
    failed_count: int = 0
    elapsed_seconds: float = 0.0


def enhance_image_blocks_with_vision(
    blocks: Sequence[ParsedBlock],
    vision_model,
    callback: Callable | None = None,
    log_prefix: str = "Image",
    lang: str = "Chinese",
    progress_start: float = 0.71,
    progress_span: float = 0.07,
) -> ImageVisionEnhancementStats:
    image_blocks = [block for block in blocks if block.type is ParsedBlockType.IMAGE]
    ready_blocks = [block for block in image_blocks if block.image is not None]
    stats = ImageVisionEnhancementStats(image_count=len(image_blocks), ready_count=len(ready_blocks))

    if not vision_model:
        if image_blocks:
            LOGGER.warning("[%s] no visual model detected; skipping image vision enhancement", log_prefix)
        return stats

    if not ready_blocks:
        LOGGER.info("[%s] no images available for vision enhancement", log_prefix)
        return stats

    started_at = time.monotonic()
    prompt = vision_llm_figure_describe_prompt(lang=getattr(vision_model, "lang", lang))
    total = len(ready_blocks)
    LOGGER.info("[%s] image vision enhancement start: total=%s", log_prefix, total)
    _callback(callback, progress_start, f"{log_prefix} image vision enhancement start: total={total}.")

    futures = {}
    for index, block in enumerate(ready_blocks, start=1):
        src = str(block.metadata.get("src", ""))
        LOGGER.info("[%s] image vision enhancement submitted: index=%s total=%s src=%s", log_prefix, index, total, src)
        # Reuse the old figure parser executor so Celery worker fork reset keeps one shared pool.
        future = figure_parser.shared_executor.submit(
            vision_llm_chunk,
            block.image,
            vision_model,
            prompt,
            callback,
        )
        futures[future] = (index, block, src, time.monotonic())

    completed_count = 0
    for future in as_completed(futures):
        index, block, src, image_started_at = futures[future]
        completed_count += 1
        progress = progress_start + (progress_span * completed_count / total)
        try:
            vision_text = future.result()
        except Exception as exc:
            stats.failed_count += 1
            elapsed = time.monotonic() - image_started_at
            LOGGER.warning(
                "[%s] image vision enhancement failed: index=%s total=%s src=%s elapsed=%.2fs error=%s",
                log_prefix,
                index,
                total,
                src,
                elapsed,
                exc,
            )
            _callback(callback, progress, f"{log_prefix} image vision enhancement failed: {completed_count}/{total}.")
            continue

        if vision_text:
            block.metadata["vision_text"] = vision_text
            stats.success_count += 1
        else:
            stats.empty_count += 1

        elapsed = time.monotonic() - image_started_at
        LOGGER.info(
            "[%s] image vision enhancement finished: index=%s total=%s src=%s elapsed=%.2fs text_chars=%s",
            log_prefix,
            index,
            total,
            src,
            elapsed,
            len(vision_text or ""),
        )
        _callback(callback, progress, f"{log_prefix} image vision enhancement finished: {completed_count}/{total}.")

    stats.elapsed_seconds = time.monotonic() - started_at
    LOGGER.info(
        "[%s] image vision enhancement summary: total=%s success=%s empty=%s failed=%s elapsed=%.2fs",
        log_prefix,
        total,
        stats.success_count,
        stats.empty_count,
        stats.failed_count,
        stats.elapsed_seconds,
    )
    _callback(
        callback,
        progress_start + progress_span,
        f"{log_prefix} image vision enhancement finished: "
        f"success={stats.success_count}, empty={stats.empty_count}, failed={stats.failed_count}.",
    )
    return stats


def _callback(callback: Callable | None, progress: float, message: str) -> None:
    if callback:
        callback(progress, message)
