import logging
import os
import tempfile
from functools import reduce

from app.core.rag.deepdoc.parser.figure_parser import VisionFigureParser
from app.core.rag.chunk.context import ChunkContext, ParseResult
from app.core.rag.chunk.parser.html import HtmlParser
from app.core.rag.chunk.parser.json import JsonParser
from app.core.rag.chunk.parser.markdown import MarkdownParser
from app.core.rag.chunk.parser.txt import TxtParser
from app.core.rag.nlp import concat_img

from .base import ChunkPipeline


class TextChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        sections = TxtParser().parse(ctx)
        ctx.callback(0.8, "Finish parsing.")
        return ParseResult(sections=sections)


class MarkdownChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        urls = set()
        section_images = None
        ctx.callback(0.1, "Start to parse.")
        markdown_parser = MarkdownParser(ctx.parser_config.get("chunk_token_num", 128))
        sections, tables = markdown_parser.parse(ctx)

        if ctx.vision_model:
            section_images = []
            for index, (section_text, _) in enumerate(sections):
                images = markdown_parser.get_pictures(section_text) if section_text else None

                if images:
                    combined_image = reduce(concat_img, images) if len(images) > 1 else images[0]
                    section_images.append(combined_image)
                    markdown_vision_parser = VisionFigureParser(
                        vision_model=ctx.vision_model,
                        figures_data=[((combined_image, ["markdown image"]), [(0, 0, 0, 0, 0)])],
                        **ctx.kwargs,
                    )
                    boosted_figures = markdown_vision_parser(callback=ctx.callback)
                    sections[index] = (
                        section_text + "\n\n" + "\n\n".join([fig[0][1][0] for fig in boosted_figures]),
                        sections[index][1],
                    )
                else:
                    section_images.append(None)
        else:
            logging.warning("No visual model detected. Skipping figure parsing enhancement.")

        if ctx.parser_config.get("hyperlink_urls", False) and ctx.is_root:
            for section_text, _ in sections:
                soup = markdown_parser.md_to_html(section_text)
                hyperlink_urls = markdown_parser.get_hyperlink_urls(soup)
                urls.update(hyperlink_urls)

        ctx.callback(0.8, "Finish parsing.")
        merge_strategy = "with_images" if section_images else "naive"
        return ParseResult(
            sections=sections,
            tables=tables,
            section_images=section_images,
            urls=urls,
            merge_strategy=merge_strategy,
        )


class HtmlChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        sections = HtmlParser().parse(ctx)
        ctx.callback(0.8, "Finish parsing.")
        return ParseResult(sections=sections)


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
