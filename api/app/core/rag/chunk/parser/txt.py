from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser import TxtParser as RAGTxtParser


class TxtParser(DocumentParser):
    def parse(self, ctx):
        return RAGTxtParser()(
            ctx.filename,
            ctx.binary,
            ctx.parser_config.get("chunk_token_num", 128),
            ctx.parser_config.get("delimiter", "\n!?;。；！？"),
        )
