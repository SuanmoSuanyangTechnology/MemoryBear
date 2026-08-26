import logging

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
