import logging
import os
import tempfile

from ..context import (
    ChunkContext,
    ImageVisionScope,
    ParsedBlock,
    ParsedBlockType,
    ParseResult,
    is_embedded_image_vision_enabled,
)
from ..parser.html import HtmlParser
from ..parser.json import JsonParser
from ..parser.structured_markdown import StructMarkdownParser
from ..parser.txt import TxtParser
from ..preprocessor import safe_log_target
from .base import ChunkPipeline

LOGGER = logging.getLogger(__name__)


class TextChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        sections = TxtParser().parse(ctx)
        ctx.callback(0.8, "Finish parsing.")
        texts = [section[0] if isinstance(section, tuple) else section for section in sections]
        content = "\n\n".join(text for text in texts if isinstance(text, str) and text.strip())
        blocks = []
        if content:
            blocks.append(
                ParsedBlock(
                    type=ParsedBlockType.TEXT,
                    content=content,
                    seq=0,
                    start_line=1,
                    end_line=content.count("\n") + 1,
                )
            )
        return ParseResult(blocks=blocks, merge_strategy="blocks")


class MarkdownChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        urls = set()
        ctx.callback(0.1, "Start to parse.")
        markdown_parser = StructMarkdownParser()
        blocks = markdown_parser.parse(ctx)

        embedded_image_vision_enabled = is_embedded_image_vision_enabled(ctx.parser_config)
        if embedded_image_vision_enabled:
            for block in blocks:
                if block.type is not ParsedBlockType.IMAGE:
                    continue
                block.image_vision_scope = ImageVisionScope.EMBEDDED
                if ctx.vision_model:
                    image = markdown_parser.load_image(str(block.metadata.get("src", "")))
                    if image:
                        block.image = image
        elif ctx.vision_model:
            LOGGER.info("Image vision enhancement disabled by parser config.")
        else:
            LOGGER.warning("No visual model detected. Skipping figure parsing enhancement.")

        if ctx.parser_config.get("hyperlink_urls", False) and ctx.is_root:
            for block in blocks:
                if block.type not in {
                    ParsedBlockType.HEADING,
                    ParsedBlockType.TEXT,
                    ParsedBlockType.LIST,
                    ParsedBlockType.BLOCKQUOTE,
                }:
                    continue
                soup = markdown_parser.md_to_html(str(block.content))
                hyperlink_urls = markdown_parser.get_hyperlink_urls(soup)
                urls.update(hyperlink_urls)

        ctx.callback(0.8, "Finish parsing.")
        LOGGER.debug("[Markdown Parsing Blocks]: count=%d", len(blocks))
        return ParseResult(
            blocks=blocks,
            urls=urls,
            merge_strategy="blocks",
            structured_markdown_stream=True,
        )


class HtmlChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        sections, tables = HtmlParser().parse(ctx)
        ctx.callback(0.8, "Finish parsing.")
        return ParseResult(sections=sections, tables=tables)


class JsonChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        sections = JsonParser().parse(ctx)
        ctx.callback(0.8, "Finish parsing.")
        return ParseResult(sections=sections)


class LegacyDocChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")

        try:
            import tika

            os.environ.setdefault("TIKA_SERVER_JAR", "/opt/tika/tika-server.jar")
            os.environ.setdefault("TIKA_SERVER_PORT", "9998")
            tika.initVM()
            from tika import parser as tika_parser
        except Exception as exc:  # noqa: BLE001 - Tika initialization can fail through optional dependencies
            ctx.callback(0.8, f"tika not available: {exc}. Unsupported .doc parsing.")
            LOGGER.warning(
                "tika unavailable target=%s error_type=%s",
                safe_log_target(ctx.filename),
                type(exc).__name__,
            )
            return ParseResult(direct_result=[], append_embed=False)

        tmp_path = None
        try:
            suffix = os.path.splitext(ctx.filename)[1] or ".doc"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_path = tmp_file.name
                if ctx.binary:
                    tmp_file.write(ctx.binary)
                else:
                    with open(ctx.filename, "rb") as file:
                        tmp_file.write(file.read())

            doc_parsed = tika_parser.from_file(tmp_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        if doc_parsed.get("content", None) is not None:
            sections = doc_parsed["content"].split("\n")
            sections = [(_, "") for _ in sections if _]
            ctx.callback(0.8, "Finish parsing.")
            return ParseResult(sections=sections)

        ctx.callback(0.8, f"tika.parser got empty content from {ctx.filename}.")
        LOGGER.warning(
            "tika parser returned empty content target=%s",
            safe_log_target(ctx.filename),
        )
        return ParseResult(direct_result=[], append_embed=False)
