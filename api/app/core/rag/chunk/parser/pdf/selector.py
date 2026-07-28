import os

from app.core.rag.chunk.context import is_image_vision_enabled
from app.core.rag.deepdoc.parser.figure_parser import vision_figure_parser_pdf_wrapper
from app.core.rag.deepdoc.parser.mineru_parser import MinerUParser
from app.core.rag.deepdoc.parser.pdf_parser import PlainParser, VisionParser

from .deepdoc import DeepDocPdfParser
from .textln import TextLnParser


def _image_vision_model(vision_model, parser_config):
    if is_image_vision_enabled(parser_config):
        return vision_model
    return None


def by_deepdoc(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, vision_model=None, pdf_cls=None, **kwargs):
    pdf_parser = pdf_cls() if pdf_cls else DeepDocPdfParser()
    sections, tables = pdf_parser(
        filename if not binary else binary,
        from_page=from_page,
        to_page=to_page,
        callback=callback,
    )

    tables = vision_figure_parser_pdf_wrapper(
        tbls=tables,
        callback=callback,
        vision_model=_image_vision_model(vision_model, kwargs.get("parser_config")),
        **kwargs,
    )
    return sections, tables, pdf_parser


def by_mineru(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, vision_model=None, pdf_cls=None, **kwargs):
    mineru_executable = os.environ.get("MINERU_EXECUTABLE", "mineru")
    mineru_api = os.environ.get("MINERU_APISERVER", "http://host.docker.internal:9987")
    pdf_parser = MinerUParser(mineru_path=mineru_executable, mineru_api=mineru_api)

    if not pdf_parser.check_installation()[0]:
        callback(-1, "MinerU not found.")
        return None, None, pdf_parser

    sections, tables = pdf_parser.parse_pdf(
        filepath=filename,
        binary=binary,
        callback=callback,
        output_dir=os.environ.get("MINERU_OUTPUT_DIR", ""),
        backend=os.environ.get("MINERU_BACKEND", "pipeline"),
        delete_output=bool(int(os.environ.get("MINERU_DELETE_OUTPUT", 1))),
    )
    return sections, tables, pdf_parser


def by_textln(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, vision_model=None, pdf_cls=None, **kwargs):
    textln_api = os.environ.get("TEXTLN_APISERVER", "https://api.textin.com/ai/service/v1/pdf_to_markdown")
    app_id = os.environ.get("TEXTLN_APP_ID", "")
    secret_code = os.environ.get("TEXTLN_SECRET_CODE", "")
    pdf_parser = TextLnParser(textln_api=textln_api, app_id=app_id, secret_code=secret_code)

    sections, tables = pdf_parser.parse_pdf(
        filepath=filename,
        binary=binary,
        callback=callback,
        vision_model=vision_model,
        lang=lang,
        **kwargs,
    )
    return sections, tables, pdf_parser


def by_plaintext(filename, binary=None, from_page=0, to_page=100000, callback=None, vision_model=None, **kwargs):
    if kwargs.get("layout_recognizer", "") == "Plain Text" or not is_image_vision_enabled(kwargs.get("parser_config")):
        pdf_parser = PlainParser()
    else:
        pdf_parser = VisionParser(vision_model=vision_model, **kwargs)

    sections, tables = pdf_parser(
        filename if not binary else binary,
        from_page=from_page,
        to_page=to_page,
        callback=callback,
    )
    return sections, tables, pdf_parser


PARSERS = {
    "deepdoc": by_deepdoc,
    "mineru": by_mineru,
    "textln": by_textln,
    "plaintext": by_plaintext,
}
