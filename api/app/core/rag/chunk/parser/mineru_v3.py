import logging
import time
from pathlib import Path

from app.core.rag.app.picture import vision_llm_chunk
from app.core.rag.chunk.context import ParsedBlockType, ParseResult
from app.core.rag.chunk.parser.base import DocumentParser
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
        attached_count = self._attach_images(blocks, mineru_result.images)
        LOGGER.info("[MinerUV3] markdown images attached: count=%s", attached_count)
        if ctx.vision_model:
            self._enhance_image_blocks(blocks, ctx)
        return ParseResult(blocks=blocks, merge_strategy="blocks")

    def _attach_images(self, blocks, images) -> int:
        attached_count = 0
        for block in blocks:
            if block.type is not ParsedBlockType.IMAGE:
                continue
            src = str(block.metadata.get("src", ""))
            image = images.get(Path(src).name)
            if image is None:
                LOGGER.warning("[MinerUV3] markdown image not found in payload: src=%s", src)
                continue
            block.image = image
            attached_count += 1
        return attached_count

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
        ctx.callback(0.71, f"MinerU V3 image vision enhancement start: total={total}.")

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
            ctx.callback(progress, f"MinerU V3 image vision enhancement: {index}/{total}.")
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
                ctx.callback(progress, f"MinerU V3 image vision enhancement failed: {index}/{total}.")
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
            ctx.callback(
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
        ctx.callback(
            0.79,
            "MinerU V3 image vision enhancement finished: "
            f"success={success_count}, empty={empty_count}, failed={failure_count}.",
        )
