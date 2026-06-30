import logging
import time
from pathlib import Path

from app.core.rag.app.picture import vision_llm_chunk
from app.core.rag.chunk.context import ParsedBlockType, ParseResult
from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.chunk.parser.image_storage import store_mineru_v3_image
from app.core.rag.chunk.parser.mineru_v3_client import MinerUV3Client
from app.core.rag.chunk.parser.structured_markdown import StructMarkdownParser
from app.core.rag.prompts.generator import vision_llm_figure_describe_prompt


LOGGER = logging.getLogger(__name__)


class MinerUV3Parser(DocumentParser):
    def __init__(self, client: MinerUV3Client | None = None):
        self.client = client or MinerUV3Client()

    def parse(self, ctx) -> ParseResult:
        binary = ctx.binary
        if binary is None:
            with open(ctx.filename, "rb") as file:
                binary = file.read()

        mineru_result = self.client.parse(
            file_name=ctx.filename,
            binary=binary,
            start_page_id=ctx.from_page,
            end_page_id=ctx.to_page,
            callback=ctx.callback,
        )
        blocks = StructMarkdownParser().parse_text(mineru_result.markdown)
        attached_count, attached_images = self._attach_images(blocks, mineru_result.images)
        LOGGER.info("[MinerUV3] markdown images attached: count=%s", attached_count)
        self._store_image_assets(attached_images, ctx)
        if ctx.vision_model:
            self._enhance_image_blocks(blocks, ctx)
        return ParseResult(blocks=blocks, merge_strategy="blocks")

    def _attach_images(self, blocks, images):
        attached_count = 0
        attached_images = []
        for block in blocks:
            if block.type is not ParsedBlockType.IMAGE:
                continue
            src = str(block.metadata.get("src", ""))
            mineru_image = images.get(Path(src).name)
            if mineru_image is None:
                LOGGER.warning("[MinerUV3] markdown image not found in payload: src=%s", src)
                continue
            block.image = mineru_image.image
            attached_images.append((block, mineru_image, src))
            attached_count += 1
        return attached_count, attached_images

    def _store_image_assets(self, attached_images, ctx) -> None:
        total = len(attached_images)
        if total == 0:
            return

        LOGGER.info("[MinerUV3] image storage start: total=%s", total)
        self._callback(ctx, 0.74, f"MinerU V3 image storage start: total={total}.")
        stored_count = 0
        failed_count = 0
        for index, (block, mineru_image, src) in enumerate(attached_images, start=1):
            try:
                asset_metadata = store_mineru_v3_image(
                    mineru_image=mineru_image,
                    tenant_id=ctx.kwargs.get("tenant_id"),
                    workspace_id=ctx.kwargs.get("workspace_id"),
                    document_id=ctx.kwargs.get("document_id"),
                    source_file_id=ctx.kwargs.get("source_file_id"),
                    source_file_name=ctx.kwargs.get("source_file_name") or ctx.filename,
                    source_src=src,
                )
            except Exception as exc:
                failed_count += 1
                LOGGER.warning(
                    "[MinerUV3] image storage failed: index=%s total=%s src=%s error=%s",
                    index,
                    total,
                    src,
                    exc,
                )
                self._callback(ctx, 0.74 + (0.03 * index / total), f"MinerU V3 image storage failed: {index}/{total}.")
                continue
            if asset_metadata:
                block.metadata.update(asset_metadata)
                stored_count += 1
                LOGGER.info(
                    "[MinerUV3] image stored: index=%s total=%s file_id=%s content_type=%s size=%s",
                    index,
                    total,
                    asset_metadata.get("image_file_id"),
                    mineru_image.content_type,
                    len(mineru_image.binary),
                )
            self._callback(ctx, 0.74 + (0.03 * index / total), f"MinerU V3 image storage: {index}/{total}.")

        LOGGER.info("[MinerUV3] image storage summary: total=%s stored=%s failed=%s", total, stored_count, failed_count)
        self._callback(ctx, 0.77, f"MinerU V3 images stored: stored={stored_count}, failed={failed_count}.")

    def _enhance_image_blocks(self, blocks, ctx) -> None:
        prompt = vision_llm_figure_describe_prompt(lang=getattr(ctx.vision_model, "lang", ctx.lang))
        image_blocks = [block for block in blocks if block.type is ParsedBlockType.IMAGE and block.image is not None]
        total = len(image_blocks)
        if total == 0:
            LOGGER.info("[MinerUV3] no markdown images available for vision enhancement")
            return

        started_at = time.monotonic()
        success_count = 0
        empty_count = 0
        failure_count = 0
        LOGGER.info("[MinerUV3] image vision enhancement start: total=%s", total)
        self._callback(ctx, 0.71, f"MinerU V3 image vision enhancement start: total={total}.")

        for index, block in enumerate(image_blocks, start=1):
            src = str(block.metadata.get("src", ""))
            image_started_at = time.monotonic()
            progress = 0.71 + (0.07 * (index - 1) / total)
            LOGGER.info(
                "[MinerUV3] image vision enhancement processing: index=%s total=%s src=%s",
                index,
                total,
                src,
            )
            self._callback(ctx, progress, f"MinerU V3 image vision enhancement: {index}/{total}.")
            try:
                vision_text = vision_llm_chunk(
                    binary=block.image,
                    vision_model=ctx.vision_model,
                    prompt=prompt,
                    callback=ctx.callback,
                )
            except Exception as exc:
                failure_count += 1
                elapsed = time.monotonic() - image_started_at
                LOGGER.warning(
                    "[MinerUV3] image vision enhancement failed: index=%s total=%s src=%s elapsed=%.2fs error=%s",
                    index,
                    total,
                    src,
                    elapsed,
                    exc,
                )
                self._callback(ctx, progress, f"MinerU V3 image vision enhancement failed: {index}/{total}.")
                continue
            if vision_text:
                block.metadata["vision_text"] = vision_text
                success_count += 1
            else:
                empty_count += 1
            elapsed = time.monotonic() - image_started_at
            LOGGER.info(
                "[MinerUV3] image vision enhancement finished: index=%s total=%s src=%s elapsed=%.2fs text_chars=%s",
                index,
                total,
                src,
                elapsed,
                len(vision_text or ""),
            )
            self._callback(
                ctx,
                0.71 + (0.07 * index / total),
                f"MinerU V3 image vision enhancement finished: {index}/{total}.",
            )

        total_elapsed = time.monotonic() - started_at
        LOGGER.info(
            "[MinerUV3] image vision enhancement summary: total=%s success=%s empty=%s failed=%s elapsed=%.2fs",
            total,
            success_count,
            empty_count,
            failure_count,
            total_elapsed,
        )
        self._callback(
            ctx,
            0.79,
            "MinerU V3 image vision enhancement finished: "
            f"success={success_count}, empty={empty_count}, failed={failure_count}.",
        )

    def _callback(self, ctx, progress, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)
