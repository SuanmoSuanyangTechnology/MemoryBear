from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser import HtmlParser as RAGHtmlParser


class HtmlParser(DocumentParser):
    def parse(self, ctx):
        sections, tables = RAGHtmlParser().parse_blocks(ctx.filename, ctx.binary)
        table_results = [((None, table.get("content", "")), "") for table in tables if table.get("content")]
        return [(_, "") for _ in sections if _], table_results
