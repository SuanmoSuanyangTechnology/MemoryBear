import re

from .pipeline.docx import DocxChunkPipeline
from .pipeline.excel import ExcelChunkPipeline
from .pipeline.image import ImageChunkPipeline
from .pipeline.media import AudioChunkPipeline, PictureVideoChunkPipeline
from .pipeline.pdf import PdfChunkPipeline, PresentationChunkPipeline
from .pipeline.text import (
    HtmlChunkPipeline,
    JsonChunkPipeline,
    LegacyDocChunkPipeline,
    MarkdownChunkPipeline,
    TextChunkPipeline,
)


class FileTypeRouter:
    def route(self, filename: str):
        if re.search(r"\.docx$", filename, re.IGNORECASE):
            return DocxChunkPipeline()
        if re.search(r"\.pdf$", filename, re.IGNORECASE):
            return PdfChunkPipeline()
        if re.search(r"\.(pptx|ppt)$", filename, re.IGNORECASE):
            return PresentationChunkPipeline()
        if re.search(r"\.(da|wave|wav|mp3|aac|flac|ogg|aiff|au|midi|wma|realaudio|vqf|oggvorbis|ape?)$", filename, re.IGNORECASE):
            return AudioChunkPipeline()
        if re.search(r"\.(png|jpeg|jpg|webp|gif)$", filename, re.IGNORECASE):
            return ImageChunkPipeline()
        if re.search(r"\.(mp4|mov|avi|flv|mpeg|mpg|webm|wmv|3gp|3gpp|mkv?)$", filename, re.IGNORECASE):
            return PictureVideoChunkPipeline()
        if re.search(r"\.(csv|xlsx?)$", filename, re.IGNORECASE):
            return ExcelChunkPipeline()
        if re.search(r"\.(txt|py|js|java|c|cpp|h|php|go|ts|sh|cs|kt|sql)$", filename, re.IGNORECASE):
            return TextChunkPipeline()
        if re.search(r"\.(md|markdown)$", filename, re.IGNORECASE):
            return MarkdownChunkPipeline()
        if re.search(r"\.(htm|html)$", filename, re.IGNORECASE):
            return HtmlChunkPipeline()
        if re.search(r"\.(json|jsonl|ldjson)$", filename, re.IGNORECASE):
            return JsonChunkPipeline()
        if re.search(r"\.doc$", filename, re.IGNORECASE):
            return LegacyDocChunkPipeline()
        raise NotImplementedError(
            "file type not supported yet(pdf, ppt, pptx, xlsx, csv, doc, docx, txt, md, html, json supported)"
        )
