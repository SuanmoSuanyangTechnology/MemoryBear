from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser import HtmlParser as RAGHtmlParser


class HtmlParser(DocumentParser):
    def parse(self, ctx):
        chunk_token_num = int(ctx.parser_config.get("chunk_token_num", 128))
        sections = RAGHtmlParser()(ctx.filename, ctx.binary, chunk_token_num)
        return [(_, "") for _ in sections if _]
