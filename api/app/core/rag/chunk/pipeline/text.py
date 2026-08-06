import logging
import os
import tempfile

from app.core.rag.chunk.context import (
    ChunkContext,
    ParsedBlock,
    ParsedBlockType,
    ParseResult,
    is_image_vision_enabled,
)
from app.core.rag.chunk.parser.html import HtmlParser
from app.core.rag.chunk.parser.image_vision import enhance_image_blocks_with_vision
from app.core.rag.chunk.parser.json import JsonParser
from app.core.rag.chunk.parser.structured_markdown import StructMarkdownParser
from app.core.rag.chunk.parser.txt import TxtParser

from .base import ChunkPipeline


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

        if ctx.vision_model and is_image_vision_enabled(ctx.parser_config):
            for block in blocks:
                if block.type is not ParsedBlockType.IMAGE:
                    continue
                image = markdown_parser.load_image(str(block.metadata.get("src", "")))
                if image:
                    block.image = image
            enhance_image_blocks_with_vision(
                blocks,
                vision_model=ctx.vision_model,
                callback=ctx.callback,
                log_prefix="Markdown",
                lang=ctx.lang,
                progress_start=0.2,
                progress_span=0.55,
            )
        elif ctx.vision_model:
            logging.info("Image vision enhancement disabled by parser config.")
        else:
            logging.warning("No visual model detected. Skipping figure parsing enhancement.")

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
        logging.debug(f"[Markdown Parsing Blocks]: {blocks}")
        return ParseResult(blocks=blocks, urls=urls, merge_strategy="blocks")


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
        except Exception as exc:
            ctx.callback(0.8, f"tika not available: {exc}. Unsupported .doc parsing.")
            logging.warning(f"tika not available: {exc}. Unsupported .doc parsing for {ctx.filename}.")
            return ParseResult(direct_result=[], append_embed=False)

        tmp_file = None
        try:
            suffix = os.path.splitext(ctx.filename)[1] or ".doc"
            tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            if ctx.binary:
                tmp_file.write(ctx.binary)
            else:
                with open(ctx.filename, "rb") as file:
                    tmp_file.write(file.read())
            tmp_file.close()

            doc_parsed = tika_parser.from_file(tmp_file.name)
        finally:
            if tmp_file and os.path.exists(tmp_file.name):
                os.unlink(tmp_file.name)

        if doc_parsed.get("content", None) is not None:
            sections = doc_parsed["content"].split("\n")
            sections = [(_, "") for _ in sections if _]
            ctx.callback(0.8, "Finish parsing.")
            return ParseResult(sections=sections)

        ctx.callback(0.8, f"tika.parser got empty content from {ctx.filename}.")
        logging.warning(f"tika.parser got empty content from {ctx.filename}.")
        return ParseResult(direct_result=[], append_embed=False)
