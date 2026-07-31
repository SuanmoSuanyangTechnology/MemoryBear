from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.chunk.context import is_image_vision_enabled
from app.core.rag.deepdoc.parser.pdf_parser import PlainParser, VisionParser


class PlainPdfParser(DocumentParser):
    def parse(self, ctx):
        layout_recognizer = ctx.parser_config.get("layout_recognize", ctx.kwargs.get("layout_recognizer", ""))
        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "Plain Text"

        if layout_recognizer == "Plain Text" or not is_image_vision_enabled(ctx.parser_config):
            pdf_parser = PlainParser()
        else:
            pdf_parser = VisionParser(vision_model=ctx.vision_model, **ctx.kwargs)

        sections, tables = pdf_parser(
            ctx.filename if not ctx.binary else ctx.binary,
            from_page=ctx.from_page,
            to_page=ctx.to_page,
            callback=ctx.callback,
        )
        return sections, tables, pdf_parser
