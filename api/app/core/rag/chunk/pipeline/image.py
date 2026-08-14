import io
import uuid
from dataclasses import replace
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.core.rag.chunk.context import (
    ChunkContext,
    ImageVisionScope,
    ParsedBlock,
    ParsedBlockType,
    ParseResult,
    is_direct_image_vision_enabled,
    is_embedded_image_vision_enabled,
)
from app.core.rag.chunk.parser.mineru_v3 import MinerUV3Parser
from app.core.rag.chunk.parser.structured_markdown import StructMarkdownParser

from .base import ChunkPipeline

DEFAULT_IMAGE_VISION_MODE = 1
IMAGE_VISION_MODES = {0, 1, 2}


class ImageChunkPipeline(ChunkPipeline):
    """Parse directly uploaded images without changing embedded-image pipelines."""

    def parse(self, ctx: ChunkContext) -> ParseResult:
        source_file_id = self._source_file_id(ctx)
        self._callback(ctx, 0.1, "Start to parse image.")

        if not is_direct_image_vision_enabled(ctx.parser_config):
            source_markdown, source_image_url = self._source_image_markdown(ctx, source_file_id)
            blocks, _ = self._parse_markdown_blocks(source_markdown, source_image_url)
            self._callback(ctx, 0.8, "Finish parsing image.")
            return ParseResult(
                blocks=blocks,
                merge_strategy="blocks",
                structured_markdown_stream=True,
                direct_image_vision_mode=None,
            )

        mode = self._image_vision_mode(ctx)
        source_binary = self._read_binary(ctx)
        analysis_binary, analysis_filename, source_image = self._analysis_input(ctx.filename, source_binary)

        mineru_blocks = []
        if mode == 0:
            mineru_blocks = self._parse_ocr_blocks(ctx, analysis_binary, analysis_filename)
        elif mode == 1:
            mineru_blocks = self._parse_ocr_blocks(
                ctx,
                analysis_binary,
                analysis_filename,
                allow_empty=True,
            )

        source_image_url = None
        markdown_parts = []
        if mode in {1, 2}:
            source_markdown, source_image_url = self._source_image_markdown(ctx, source_file_id)
            markdown_parts.append(source_markdown)

        blocks, source_image_block = self._parse_markdown_blocks(
            "\n\n".join(markdown_parts),
            source_image_url,
        )
        blocks.extend(mineru_blocks)
        has_ocr_text = self._has_content(
            [block for block in mineru_blocks if block.type is not ParsedBlockType.IMAGE]
        )
        if mode in {1, 2}:
            source_image_block.image = source_image
            source_image_block.image_vision_scope = ImageVisionScope.DIRECT

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

    def _image_vision_mode(self, ctx: ChunkContext) -> int:
        image_config = ctx.parser_config.get("image")
        raw_mode = image_config.get("vision_mode", DEFAULT_IMAGE_VISION_MODE) if isinstance(image_config, dict) else DEFAULT_IMAGE_VISION_MODE
        if type(raw_mode) is not int or raw_mode not in IMAGE_VISION_MODES:
            raise ValueError("parser_config.image.vision_mode must be an integer in {0, 1, 2}.")
        return raw_mode

    def _read_binary(self, ctx: ChunkContext) -> bytes:
        if ctx.binary is not None:
            return ctx.binary
        with open(ctx.filename, "rb") as file:
            return file.read()

    def _analysis_input(self, filename: str, source_binary: bytes) -> tuple[bytes, str, Image.Image]:
        with Image.open(io.BytesIO(source_binary)) as image:
            image.seek(0)
            source_image = image.convert("RGB").copy()

        if Path(filename).suffix.lower() != ".gif":
            return source_binary, filename, source_image

        first_frame = source_image.convert("RGB")
        image_binary = io.BytesIO()
        first_frame.save(image_binary, format="PNG")
        analysis_filename = str(Path(filename).with_suffix(".png"))
        return image_binary.getvalue(), analysis_filename, first_frame

    def _parse_ocr_blocks(
        self,
        ctx: ChunkContext,
        analysis_binary: bytes,
        analysis_filename: str,
        *,
        allow_empty: bool = False,
    ) -> list[ParsedBlock]:
        ocr_ctx = replace(
            ctx,
            filename=analysis_filename,
            binary=analysis_binary,
        )
        blocks = MinerUV3Parser().parse(ocr_ctx).blocks or []
        if not blocks:
            if allow_empty:
                return []
            raise ValueError("MinerU returned empty Markdown for image OCR mode.")
        if is_embedded_image_vision_enabled(ctx.parser_config):
            return blocks
        return [block for block in blocks if block.type is not ParsedBlockType.IMAGE]

    def _source_file_id(self, ctx: ChunkContext) -> str:
        raw_file_id = ctx.kwargs.get("source_file_id")
        if not raw_file_id:
            raise ValueError("source_file_id is required to create an image chunk.")
        try:
            return str(uuid.UUID(str(raw_file_id)))
        except (TypeError, ValueError) as exc:
            raise ValueError("source_file_id must be a valid UUID for image chunking.") from exc

    def _source_image_markdown(self, ctx: ChunkContext, source_file_id: str) -> tuple[str, str]:
        filename = Path(ctx.kwargs.get("source_file_name") or ctx.filename).name
        source_image_url = self._source_file_url(source_file_id)
        return f"![{filename}]({source_image_url})", source_image_url

    def _parse_markdown_blocks(
        self,
        markdown: str,
        source_image_url: str | None,
    ) -> tuple[list[ParsedBlock], ParsedBlock | None]:
        blocks = StructMarkdownParser().parse_text(markdown, normalize_escaped_structure=True)
        source_image_block = None
        filtered_blocks = []
        for block in blocks:
            if block.type is not ParsedBlockType.IMAGE:
                filtered_blocks.append(block)
                continue
            if source_image_url and source_image_block is None and block.metadata.get("src") == source_image_url:
                source_image_block = block
                filtered_blocks.append(block)

        if source_image_url and source_image_block is None:
            raise ValueError("Failed to parse the source image Markdown tag.")
        return filtered_blocks, source_image_block

    def _source_file_url(self, source_file_id) -> str:
        server_url = (settings.FILE_LOCAL_SERVER_URL or "").rstrip("/")
        path = f"/files/{source_file_id}"
        return f"{server_url}{path}" if server_url else path

    def _has_content(self, blocks: list[ParsedBlock]) -> bool:
        return any(str(block.content or "").strip() for block in blocks)

    def _callback(self, ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)
