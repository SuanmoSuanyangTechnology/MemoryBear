from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser.utils import get_text


class TxtParser(DocumentParser):
    def parse(self, ctx):
        text = get_text(ctx.filename, ctx.binary)
        return [(text, "")] if text else []
