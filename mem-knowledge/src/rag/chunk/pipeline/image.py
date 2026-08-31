"""Direct image pipeline with MinerU-only OCR and three vision modes."""

from __future__ import annotations

import io
import uuid
from dataclasses import replace
from pathlib import Path

from PIL import Image

from ....bootstrap import get_settings
from ..context import (
    ChunkContext,
    ImageVisionScope,
    ParsedBlock,
    ParsedBlockType,
    ParseResult,
    is_direct_image_vision_enabled,
    is_embedded_image_vision_enabled,
)
from ..parser.mineru_v3 import MinerUV3Parser
from ..parser.structured_markdown import StructMarkdownParser
from .base import ChunkPipeline

DEFAULT_IMAGE_VISION_MODE = 1
IMAGE_VISION_MODES = {0, 1, 2}


class ImageChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        source_file_id = self._source_file_id(ctx)
        self._callback(ctx, 0.1, "Start to parse image.")
        if not is_direct_image_vision_enabled(ctx.parser_config):
            source_markdown, source_url = self._source_image_markdown(ctx, source_file_id)
            blocks, _source_block = self._parse_markdown_blocks(source_markdown, source_url)
            self._callback(ctx, 0.8, "Finish parsing image.")
            return ParseResult(
                blocks=blocks,
                merge_strategy="blocks",
                structured_markdown_stream=True,
                direct_image_vision_mode=None,
            )

        mode = self._image_vision_mode(ctx)
        source_binary = self._read_binary(ctx)
        analysis_binary, analysis_filename, source_image = self._analysis_input(
            ctx.filename,
            source_binary,
        )
        ocr_blocks: list[ParsedBlock] = []
        if mode in {0, 1}:
            ocr_blocks = self._parse_ocr_blocks(
                ctx,
                analysis_binary,
                analysis_filename,
                allow_empty=mode == 1,
            )

        source_url = None
        source_markdown = ""
        if mode in {1, 2}:
            source_markdown, source_url = self._source_image_markdown(ctx, source_file_id)
        blocks, source_block = self._parse_markdown_blocks(source_markdown, source_url)
        has_ocr_text = self._has_content(
            [block for block in ocr_blocks if block.type is not ParsedBlockType.IMAGE]
        )
        if (
            mode == 1
            and not has_ocr_text
            and len(ocr_blocks) == 1
            and ocr_blocks[0].type is ParsedBlockType.IMAGE
        ):
            ocr_blocks = []
        blocks.extend(ocr_blocks)
        if source_block is not None:
            source_block.image = source_image
            source_block.image_vision_scope = ImageVisionScope.DIRECT
        if mode == 0 and not has_ocr_text:
            raise ValueError("MinerU returned no text content for image OCR mode.")
        self._callback(ctx, 0.8, "Finish parsing image.")
        return ParseResult(
            blocks=blocks,
            merge_strategy="blocks",
            structured_markdown_stream=True,
            direct_image_vision_mode=mode,
            direct_image_has_ocr_text=has_ocr_text,
        )

    @staticmethod
    def _image_vision_mode(ctx: ChunkContext) -> int:
        image_config = ctx.parser_config.get("image")
        raw_mode = (
            image_config.get("vision_mode", DEFAULT_IMAGE_VISION_MODE)
            if isinstance(image_config, dict)
            else DEFAULT_IMAGE_VISION_MODE
        )
        if type(raw_mode) is not int or raw_mode not in IMAGE_VISION_MODES:
            raise ValueError("parser_config.image.vision_mode must be an integer in {0, 1, 2}.")
        return raw_mode

    @staticmethod
    def _read_binary(ctx: ChunkContext) -> bytes:
        if ctx.binary is not None:
            return ctx.binary
        return Path(ctx.filename).read_bytes()

    @staticmethod
    def _analysis_input(filename: str, binary: bytes) -> tuple[bytes, str, Image.Image]:
        with Image.open(io.BytesIO(binary)) as image:
            image.seek(0)
            source_image = image.convert("RGB").copy()
        if Path(filename).suffix.lower() != ".gif":
            return binary, filename, source_image
        output = io.BytesIO()
        source_image.save(output, format="PNG")
        return output.getvalue(), str(Path(filename).with_suffix(".png")), source_image

    @staticmethod
    def _parse_ocr_blocks(
        ctx: ChunkContext,
        analysis_binary: bytes,
        analysis_filename: str,
        *,
        allow_empty: bool,
    ) -> list[ParsedBlock]:
        ocr_ctx = replace(ctx, filename=analysis_filename, binary=analysis_binary)
        blocks = MinerUV3Parser().parse(ocr_ctx).blocks or []
        if not blocks and not allow_empty:
            raise ValueError("MinerU returned empty Markdown for image OCR mode.")
        if is_embedded_image_vision_enabled(ctx.parser_config):
            return blocks
        return [block for block in blocks if block.type is not ParsedBlockType.IMAGE]

    @staticmethod
    def _source_file_id(ctx: ChunkContext) -> str:
        value = ctx.kwargs.get("source_file_id")
        if not value:
            raise ValueError("source_file_id is required to create an image chunk.")
        try:
            return str(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            raise ValueError("source_file_id must be a valid UUID for image chunking.") from None

    def _source_image_markdown(self, ctx: ChunkContext, file_id: str) -> tuple[str, str]:
        filename = Path(ctx.kwargs.get("source_file_name") or ctx.filename).name
        prefix = get_settings().file_local_server_url.rstrip("/")
        path = f"/files/{file_id}"
        url = f"{prefix}{path}" if prefix else path
        return f"![{filename}]({url})", url

    @staticmethod
    def _parse_markdown_blocks(
        markdown: str,
        source_url: str | None,
    ) -> tuple[list[ParsedBlock], ParsedBlock | None]:
        blocks = StructMarkdownParser().parse_text(
            markdown,
            normalize_escaped_structure=True,
        )
        source_block = None
        filtered = []
        for block in blocks:
            if block.type is not ParsedBlockType.IMAGE:
                filtered.append(block)
            elif source_url and source_block is None and block.metadata.get("src") == source_url:
                source_block = block
                filtered.append(block)
        if source_url and source_block is None:
            raise ValueError("Failed to parse the source image Markdown tag.")
        return filtered, source_block

    @staticmethod
    def _has_content(blocks: list[ParsedBlock]) -> bool:
        return any(str(block.content or "").strip() for block in blocks)

    @staticmethod
    def _callback(ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


__all__ = ["ImageChunkPipeline"]
