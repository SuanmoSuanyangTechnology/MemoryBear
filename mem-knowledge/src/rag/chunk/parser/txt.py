from ..tokenization import find_codec
from .base import DocumentParser


class TxtParser(DocumentParser):
    def parse(self, ctx):
        if ctx.binary is not None:
            text = ctx.binary.decode(find_codec(ctx.binary), errors="ignore")
        else:
            with open(ctx.filename) as file:
                text = file.read()
        return [(text, "")] if text else []


__all__ = ["TxtParser"]
