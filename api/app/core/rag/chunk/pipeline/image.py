import io
import logging
import uuid
from dataclasses import replace
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.core.rag.app.picture import vision_llm_chunk
from app.core.rag.chunk.context import (
    ChunkContext,
    ParsedBlock,
    ParsedBlockType,
    ParseResult,
)
from app.core.rag.chunk.parser.mineru_v3 import MinerUV3Parser
from app.core.rag.prompts.generator import vision_llm_figure_describe_prompt

from .base import ChunkPipeline

LOGGER = logging.getLogger(__name__)
DEFAULT_IMAGE_VISION_MODE = 1
IMAGE_VISION_MODES = {0, 1, 2}


class ImageChunkPipeline(ChunkPipeline):
    """Parse directly uploaded images without changing embedded-image pipelines."""

    def parse(self, ctx: ChunkContext) -> ParseResult:
        mode = self._image_vision_mode(ctx)
        source_file_id = self._source_file_id(ctx)
        source_binary = self._read_binary(ctx)
        analysis_binary, analysis_filename, source_image = self._analysis_input(ctx.filename, source_binary)

        self._callback(ctx, 0.1, "Start to parse image.")
        text_blocks: list[ParsedBlock] = []
        if mode in {0, 1}:
            text_blocks = self._parse_ocr_text(ctx, analysis_binary, analysis_filename)

        vision_text = ""
        if mode in {1, 2}:
            vision_text = self._describe_source_image(ctx, source_image)

        if mode == 0:
            if not self._has_content(text_blocks):
                raise ValueError("MinerU returned no text content for image OCR mode.")
            blocks = text_blocks
        elif mode == 1:
            if not self._has_content(text_blocks) and not vision_text:
                raise ValueError("Image mixed mode produced neither OCR text nor visual description.")
            blocks = [self._source_image_block(ctx, source_file_id, vision_text), *text_blocks]
        else:
            if not vision_text:
                raise ValueError("Image pure vision mode produced no visual description.")
            blocks = [self._source_image_block(ctx, source_file_id, vision_text)]

        self._callback(ctx, 0.8, "Finish parsing image.")
        return ParseResult(blocks=blocks, merge_strategy="blocks")

    def _image_vision_mode(self, ctx: ChunkContext) -> int:
        raw_mode = ctx.parser_config.get("image_vision_mode", DEFAULT_IMAGE_VISION_MODE)
        if type(raw_mode) is not int or raw_mode not in IMAGE_VISION_MODES:
            raise ValueError("parser_config.image_vision_mode must be an integer in {0, 1, 2}.")
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

    def _parse_ocr_text(
        self,
        ctx: ChunkContext,
        analysis_binary: bytes,
        analysis_filename: str,
    ) -> list[ParsedBlock]:
        parser_config = dict(ctx.parser_config)
        parser_config["image_vision_enabled"] = False
        ocr_ctx = replace(
            ctx,
            filename=analysis_filename,
            binary=analysis_binary,
            parser_config=parser_config,
        )
        result = MinerUV3Parser().parse(ocr_ctx)
        blocks = result.blocks or []
        if not blocks:
            raise ValueError("MinerU returned empty Markdown for image OCR mode.")
        return [block for block in blocks if block.type is not ParsedBlockType.IMAGE]

    def _describe_source_image(self, ctx: ChunkContext, source_image: Image.Image) -> str:
        prompt = vision_llm_figure_describe_prompt(lang=getattr(ctx.vision_model, "lang", ctx.lang))
        try:
            vision_text = vision_llm_chunk(source_image, ctx.vision_model, prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[ImagePipeline] source image vision failed: file_name=%s error=%s", ctx.filename, exc)
            return ""

        vision_text = str(vision_text or "").strip()
        if not vision_text:
            LOGGER.warning("[ImagePipeline] source image vision returned empty: file_name=%s", ctx.filename)
        return vision_text

    def _source_file_id(self, ctx: ChunkContext) -> str:
        raw_file_id = ctx.kwargs.get("source_file_id")
        if not raw_file_id:
            raise ValueError("source_file_id is required to create an image chunk.")
        try:
            return str(uuid.UUID(str(raw_file_id)))
        except (TypeError, ValueError) as exc:
            raise ValueError("source_file_id must be a valid UUID for image chunking.") from exc

    def _source_image_block(self, ctx: ChunkContext, source_file_id: str, vision_text: str) -> ParsedBlock:
        filename = Path(ctx.kwargs.get("source_file_name") or ctx.filename).name
        content = f"![{filename}]({self._source_file_url(source_file_id)})"
        metadata = {"vision_text": vision_text} if vision_text else {}
        return ParsedBlock(type=ParsedBlockType.IMAGE, content=content, metadata=metadata)

    def _source_file_url(self, source_file_id) -> str:
        server_url = (settings.FILE_LOCAL_SERVER_URL or "").rstrip("/")
        path = f"/storage/permanent/{source_file_id}"
        return f"{server_url}{path}" if server_url else path

    def _has_content(self, blocks: list[ParsedBlock]) -> bool:
        return any(str(block.content or "").strip() for block in blocks)

    def _callback(self, ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)
