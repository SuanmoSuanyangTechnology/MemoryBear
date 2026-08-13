import logging
import uuid
from pathlib import Path

from app.core.rag.chunk.context import (
    ImageVisionScope,
    ParsedBlockType,
    ParseResult,
    is_embedded_image_vision_enabled,
)
from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.chunk.parser.image_storage import (
    cleanup_mineru_v3_images,
    store_mineru_v3_image,
)
from app.core.rag.chunk.parser.mineru_v3_client import MinerUV3Client
from app.core.rag.chunk.parser.structured_markdown import StructMarkdownParser

LOGGER = logging.getLogger(__name__)


class MinerUV3Parser(DocumentParser):
    def __init__(self, client: MinerUV3Client | None = None):
        self.client = client or MinerUV3Client()

    def parse(self, ctx) -> ParseResult:
        binary = self._read_binary(ctx)

        embedded_image_vision_enabled = is_embedded_image_vision_enabled(ctx.parser_config)
        mineru_result = self.client.parse(
            file_name=ctx.filename,
            binary=binary,
            start_page_id=ctx.from_page,
            end_page_id=ctx.to_page,
            callback=ctx.callback,
            return_images=embedded_image_vision_enabled,
        )
        blocks = StructMarkdownParser().parse_text(
            mineru_result.markdown,
            normalize_escaped_structure=True,
        )
        if embedded_image_vision_enabled:
            for block in blocks:
                if block.type is ParsedBlockType.IMAGE:
                    block.image_vision_scope = ImageVisionScope.EMBEDDED
            attached_count, attached_images, unresolved_count = self._attach_images(blocks, mineru_result.images)
            LOGGER.info("[MinerUV3] markdown images attached: count=%s", attached_count)
            retained_file_ids, all_assets_stored = self._store_image_assets(attached_images, ctx)
            all_assets_stored = all_assets_stored and unresolved_count == 0
        else:
            LOGGER.info("[MinerUV3] image block processing disabled by parser config")
            retained_file_ids = set()
            all_assets_stored = True

        if all_assets_stored:
            self._cleanup_stale_image_assets(ctx, retained_file_ids)
        else:
            LOGGER.warning("[MinerUV3] stale image cleanup skipped because one or more image assets were not stored")

        if ctx.vision_model and not embedded_image_vision_enabled:
            LOGGER.info("[MinerUV3] image vision enhancement disabled by parser config")
        return ParseResult(
            blocks=blocks,
            merge_strategy="blocks",
            markdown_preprocess_profile="mineru",
            structured_markdown_stream=True,
        )

    def parse_markdown(self, ctx) -> str:
        binary = self._read_binary(ctx)
        mineru_result = self.client.parse(
            file_name=ctx.filename,
            binary=binary,
            start_page_id=ctx.from_page,
            end_page_id=ctx.to_page,
            callback=ctx.callback,
            return_images=False,
        )
        return mineru_result.markdown

    def _read_binary(self, ctx) -> bytes:
        if ctx.binary is not None:
            return ctx.binary
        with open(ctx.filename, "rb") as file:
            return file.read()

    def _attach_images(self, blocks, images):
        attached_count = 0
        attached_images = []
        unresolved_count = 0
        for block in blocks:
            if block.type is not ParsedBlockType.IMAGE:
                continue
            src = str(block.metadata.get("src", ""))
            mineru_image = images.get(Path(src).name)
            if mineru_image is None:
                LOGGER.warning("[MinerUV3] markdown image not found in payload: src=%s", src)
                unresolved_count += 1
                continue
            block.image = mineru_image.image
            attached_images.append((block, mineru_image, src))
            attached_count += 1
        return attached_count, attached_images, unresolved_count

    def _store_image_assets(self, attached_images, ctx) -> tuple[set[uuid.UUID], bool]:
        total = len(attached_images)
        if total == 0:
            return set(), True

        LOGGER.info("[MinerUV3] image storage start: total=%s", total)
        self._callback(ctx, 0.74, f"MinerU V3 image storage start: total={total}.")
        stored_count = 0
        failed_count = 0
        stored_file_ids: set[uuid.UUID] = set()
        for index, (block, mineru_image, src) in enumerate(attached_images, start=1):
            try:
                asset = store_mineru_v3_image(
                    mineru_image=mineru_image,
                    tenant_id=ctx.kwargs.get("tenant_id"),
                    workspace_id=ctx.kwargs.get("workspace_id"),
                    document_id=ctx.kwargs.get("document_id"),
                    source_file_id=ctx.kwargs.get("source_file_id"),
                    source_file_name=ctx.kwargs.get("source_file_name") or ctx.filename,
                    source_src=src,
                )
                if asset is None:
                    raise ValueError("image asset was not created")
                self._replace_image_markdown_url(block, asset.download_url)
                stored_file_ids.add(asset.file_id)
            except Exception as exc:  # noqa: BLE001 - isolate one asset failure from the batch
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
            stored_count += 1
            LOGGER.info(
                "[MinerUV3] image stored: index=%s total=%s file_id=%s content_type=%s size=%s",
                index,
                total,
                asset.file_id,
                mineru_image.content_type,
                len(mineru_image.binary),
            )
            self._callback(ctx, 0.74 + (0.03 * index / total), f"MinerU V3 image storage: {index}/{total}.")

        LOGGER.info("[MinerUV3] image storage summary: total=%s stored=%s failed=%s", total, stored_count, failed_count)
        self._callback(ctx, 0.77, f"MinerU V3 images stored: stored={stored_count}, failed={failed_count}.")
        return stored_file_ids, failed_count == 0 and stored_count == total

    def _replace_image_markdown_url(self, block, image_url: str | None) -> None:
        if not image_url:
            return
        alt = str(block.metadata.get("alt") or "")
        block.content = f"![{alt}]({image_url})"
        block.metadata["src"] = image_url

    def _cleanup_stale_image_assets(self, ctx, retained_file_ids: set[uuid.UUID]) -> None:
        document_id = ctx.kwargs.get("document_id")
        try:
            document_uuid = document_id if isinstance(document_id, uuid.UUID) else uuid.UUID(str(document_id))
        except (TypeError, ValueError):
            LOGGER.warning("[MinerUV3] stale image cleanup skipped: invalid document_id=%s", document_id)
            return
        try:
            deleted_count = cleanup_mineru_v3_images(document_uuid, retained_file_ids)
        except Exception:
            LOGGER.warning(
                "[MinerUV3] stale image cleanup failed: document_id=%s",
                document_uuid,
                exc_info=True,
            )
            return
        if deleted_count:
            LOGGER.info("[MinerUV3] stale image assets deleted: document_id=%s count=%s", document_uuid, deleted_count)

    def _callback(self, ctx, progress, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)
