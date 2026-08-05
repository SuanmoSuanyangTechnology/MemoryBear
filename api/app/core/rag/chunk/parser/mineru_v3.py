import logging
from pathlib import Path

from app.core.rag.chunk.context import ParsedBlockType, ParseResult, is_image_vision_enabled
from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.chunk.parser.image_vision import enhance_image_blocks_with_vision
from app.core.rag.chunk.parser.image_storage import store_mineru_v3_image
from app.core.rag.chunk.parser.mineru_v3_client import MinerUV3Client
from app.core.rag.chunk.parser.structured_markdown import StructMarkdownParser


LOGGER = logging.getLogger(__name__)


class MinerUV3Parser(DocumentParser):
    def __init__(self, client: MinerUV3Client | None = None):
        self.client = client or MinerUV3Client()

    def parse(self, ctx) -> ParseResult:
        binary = ctx.binary
        if binary is None:
            with open(ctx.filename, "rb") as file:
                binary = file.read()

        image_vision_enabled = is_image_vision_enabled(ctx.parser_config)
        mineru_result = self.client.parse(
            file_name=ctx.filename,
            binary=binary,
            start_page_id=ctx.from_page,
            end_page_id=ctx.to_page,
            callback=ctx.callback,
            return_images=image_vision_enabled,
        )
        blocks = StructMarkdownParser().parse_text(
            mineru_result.markdown,
            normalize_escaped_structure=True,
        )
        if image_vision_enabled:
            attached_count, attached_images = self._attach_images(blocks, mineru_result.images)
            LOGGER.info("[MinerUV3] markdown images attached: count=%s", attached_count)
            self._store_image_assets(attached_images, ctx)
        else:
            LOGGER.info("[MinerUV3] image block processing disabled by parser config")

        if ctx.vision_model and image_vision_enabled:
            self._enhance_image_blocks(blocks, ctx)
        elif ctx.vision_model:
            LOGGER.info("[MinerUV3] image vision enhancement disabled by parser config")
        return ParseResult(blocks=blocks, merge_strategy="blocks", markdown_preprocess_profile="mineru")

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
                self._replace_image_markdown_url(block, asset_metadata.get("image_download_url"))
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

    def _replace_image_markdown_url(self, block, image_url: str | None) -> None:
        if not image_url:
            return
        alt = str(block.metadata.get("alt") or "")
        block.content = f"![{alt}]({image_url})"

    def _enhance_image_blocks(self, blocks, ctx) -> None:
        enhance_image_blocks_with_vision(
            blocks,
            vision_model=ctx.vision_model,
            callback=ctx.callback,
            log_prefix="MinerUV3",
            lang=ctx.lang,
            progress_start=0.77,
            progress_span=0.02,
        )

    def _callback(self, ctx, progress, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)
