from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser import ExcelParser as RAGExcelParser


class ExcelParser(DocumentParser):
    def parse(self, ctx):
        binary = ctx.binary
        if not binary:
            with open(ctx.filename, "rb") as file:
                binary = file.read()

        excel_parser = RAGExcelParser()
        if ctx.parser_config.get("html4excel") and ctx.parser_config.get("html4excel").lower() == "true":
            return [(_, "") for _ in excel_parser.html(binary, 12) if _]
        return [(_, "") for _ in excel_parser(binary) if _]
