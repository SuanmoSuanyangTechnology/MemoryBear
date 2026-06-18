import os
import tempfile

from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser import JsonParser as RAGJsonParser


class JsonParser(DocumentParser):
    def parse(self, ctx):
        chunk_token_num = int(ctx.parser_config.get("chunk_token_num", 128))
        if ctx.binary:
            tmp_file = None
            try:
                suffix = os.path.splitext(ctx.filename)[1] or ".json"
                tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb")
                tmp_file.write(ctx.binary)
                tmp_file.close()
                sections = RAGJsonParser(chunk_token_num)(tmp_file.name)
            finally:
                if tmp_file and os.path.exists(tmp_file.name):
                    os.unlink(tmp_file.name)
        else:
            sections = RAGJsonParser(chunk_token_num)(ctx.filename)
        return [(_, "") for _ in sections if _]
