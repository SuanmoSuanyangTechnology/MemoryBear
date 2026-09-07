"""MinerU V3 parser producing service-owned structured Markdown blocks."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from ..context import (
    ImageVisionScope,
    ParsedBlockType,
    ParseResult,
    is_embedded_image_vision_enabled,
)
from .base import DocumentParser
from .image_storage import cleanup_mineru_v3_images, store_mineru_v3_image
from .mineru_v3_client import MinerUV3Client
from .structured_markdown import StructMarkdownParser

LOGGER = logging.getLogger(__name__)


class MinerUV3Parser(DocumentParser):
    def __init__(self, client: MinerUV3Client | None = None) -> None:
        self.client = client or MinerUV3Client()

    def parse(self, ctx) -> ParseResult:
        embedded_vision = is_embedded_image_vision_enabled(ctx.parser_config)
        result = self.client.parse(
            file_name=ctx.filename,
            binary=self._read_binary(ctx),
            start_page_id=ctx.from_page,
            end_page_id=ctx.to_page,
            callback=ctx.callback,
            return_images=embedded_vision,
        )
        blocks = StructMarkdownParser().parse_text(
            result.markdown,
            normalize_escaped_structure=True,
        )
        retained_file_ids: set[uuid.UUID] = set()
        storage_complete = True
        if embedded_vision:
            attached, unresolved = self._attach_images(blocks, result.images)
            for block in blocks:
                if block.type is ParsedBlockType.IMAGE:
                    block.image_vision_scope = ImageVisionScope.EMBEDDED
            retained_file_ids, stored = self._store_image_assets(attached, ctx)
            storage_complete = stored and unresolved == 0
        if storage_complete:
            self._cleanup_stale_image_assets(ctx, retained_file_ids)
        else:
            LOGGER.warning("MinerU stale image cleanup skipped after incomplete asset storage")
        return ParseResult(
            blocks=blocks,
            merge_strategy="blocks",
            markdown_preprocess_profile="mineru",
            structured_markdown_stream=True,
        )

    def parse_markdown(self, ctx) -> str:
        return self.client.parse_to_markdown(
            ctx.filename,
            self._read_binary(ctx),
            ctx.from_page,
            ctx.to_page,
            ctx.callback,
        )

    @staticmethod
    def _read_binary(ctx) -> bytes:
        if ctx.binary is not None:
            return ctx.binary
        return Path(ctx.filename).read_bytes()

    @staticmethod
    def _attach_images(blocks, images) -> tuple[list[tuple], int]:
        attached = []
        unresolved = 0
        for block in blocks:
            if block.type is not ParsedBlockType.IMAGE:
                continue
            source = str(block.metadata.get("src") or "")
            image = images.get(Path(source).name)
            if image is None:
                unresolved += 1
                LOGGER.warning("MinerU Markdown image was absent from the image payload")
                continue
            block.image = image.image
            attached.append((block, image, source))
        return attached, unresolved

    def _store_image_assets(self, attached, ctx) -> tuple[set[uuid.UUID], bool]:
        retained: set[uuid.UUID] = set()
        failures = 0
        for block, mineru_image, source in attached:
            try:
                asset = store_mineru_v3_image(
                    mineru_image=mineru_image,
                    tenant_id=ctx.kwargs.get("tenant_id"),
                    workspace_id=ctx.kwargs.get("workspace_id"),
                    document_id=ctx.kwargs.get("document_id"),
                    source_file_id=ctx.kwargs.get("source_file_id"),
                    source_file_name=ctx.kwargs.get("source_file_name") or ctx.filename,
                    source_src=source,
                    runtime=ctx.kwargs.get("runtime"),
                )
                if asset is None:
                    raise RuntimeError("image asset context was incomplete")
                alt = str(block.metadata.get("alt") or "")
                block.content = f"![{alt}]({asset.download_url})"
                block.metadata["src"] = asset.download_url
                block.metadata["asset_file_id"] = str(asset.file_id)
                retained.add(asset.file_id)
            except Exception as exc:  # noqa: BLE001 - preserve remaining image assets.
                failures += 1
                LOGGER.warning(
                    "MinerU image storage failed error_type=%s",
                    type(exc).__name__,
                )
        return retained, failures == 0 and len(retained) == len(attached)

    @staticmethod
    def _cleanup_stale_image_assets(ctx, retained_file_ids: set[uuid.UUID]) -> None:
        try:
            document_id = uuid.UUID(str(ctx.kwargs.get("document_id")))
        except (TypeError, ValueError):
            return
        try:
            cleanup_mineru_v3_images(
                document_id,
                retained_file_ids,
                runtime=ctx.kwargs.get("runtime"),
            )
        except Exception as exc:  # noqa: BLE001 - stale cleanup is best effort.
            LOGGER.warning(
                "MinerU stale image cleanup failed error_type=%s",
                type(exc).__name__,
            )


__all__ = ["MinerUV3Parser"]
