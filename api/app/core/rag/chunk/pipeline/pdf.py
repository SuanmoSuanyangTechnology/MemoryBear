import os
import tempfile

from app.core.rag.chunk.context import ChunkContext, ParseResult
from app.core.rag.chunk.parser.pdf import DeepDocPdfParser, MinerUPdfParser, PlainPdfParser, TextLnPdfParser
from app.core.rag.utils.file_utils import extract_links_from_pdf
from app.core.rag.utils.libre_office import async_convert_to_pdf

from .base import ChunkPipeline


class PdfChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        urls = set()
        layout_recognizer = ctx.parser_config.get("layout_recognize", "DeepDOC")
        if ctx.parser_config.get("analyze_hyperlink", False) and ctx.is_root:
            urls = extract_links_from_pdf(ctx.binary)

        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "Plain Text"

        name = layout_recognizer.strip().lower()
        parser = self.select_parser(name)
        ctx.callback(0.1, "Start to parse.")

        sections, tables, pdf_parser = parser.parse(ctx)

        if not sections and not tables:
            return ParseResult(direct_result=[], append_embed=False)

        if name in ["mineru", "textln"] and not ctx.kwargs.get("_keep_chunk_token_num"):
            ctx.parser_config["chunk_token_num"] = 0

        ctx.callback(0.8, "Finish parsing.")
        return ParseResult(sections=sections, tables=tables, pdf_parser=pdf_parser, urls=urls)

    def select_parser(self, name):
        if name == "deepdoc":
            return DeepDocPdfParser()
        if name == "mineru":
            return MinerUPdfParser()
        if name == "textln":
            return TextLnPdfParser()
        return PlainPdfParser()


class PresentationChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        tmp_file = None
        dest_pdf_path = None
        try:
            suffix = os.path.splitext(ctx.filename)[1] or ".pptx"
            tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            if ctx.binary:
                tmp_file.write(ctx.binary)
            else:
                with open(ctx.filename, "rb") as file:
                    tmp_file.write(file.read())
            tmp_file.close()

            future = async_convert_to_pdf(tmp_file.name)
            dest_pdf_path = future.result()
            direct_result = self.run_child(
                dest_pdf_path,
                binary=None,
                ctx=ctx,
                is_root=ctx.kwargs.get("is_root", True),
                vision_model=ctx.vision_model,
            )
            return ParseResult(direct_result=direct_result, append_embed=False)
        finally:
            if tmp_file and os.path.exists(tmp_file.name):
                os.unlink(tmp_file.name)
            if dest_pdf_path and os.path.exists(dest_pdf_path):
                os.unlink(dest_pdf_path)
