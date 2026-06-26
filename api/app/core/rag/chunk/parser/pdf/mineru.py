import os

from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.deepdoc.parser.mineru_parser import MinerUParser


class MinerUPdfParser(DocumentParser):
    def parse(self, ctx):
        mineru_executable = os.environ.get("MINERU_EXECUTABLE", "mineru")
        mineru_api = os.environ.get("MINERU_APISERVER", "http://host.docker.internal:9987")
        pdf_parser = MinerUParser(mineru_path=mineru_executable, mineru_api=mineru_api)

        if not pdf_parser.check_installation()[0]:
            ctx.callback(-1, "MinerU not found.")
            return None, None, pdf_parser

        sections, tables = pdf_parser.parse_pdf(
            filepath=ctx.filename,
            binary=ctx.binary,
            callback=ctx.callback,
            output_dir=os.environ.get("MINERU_OUTPUT_DIR", ""),
            backend=os.environ.get("MINERU_BACKEND", "pipeline"),
            delete_output=bool(int(os.environ.get("MINERU_DELETE_OUTPUT", 1))),
        )
        return sections, tables, pdf_parser
