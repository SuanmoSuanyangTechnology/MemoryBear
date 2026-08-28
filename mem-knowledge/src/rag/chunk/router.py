import re


class FileTypeRouter:
    def route(self, filename: str):
        if re.search(r"\.docx$", filename, re.IGNORECASE):
            from .pipeline.document import DocxChunkPipeline

            return DocxChunkPipeline()
        if re.search(r"\.pdf$", filename, re.IGNORECASE):
            from .pipeline.pdf import PdfChunkPipeline

            return PdfChunkPipeline()
        if re.search(r"\.(pptx|ppt)$", filename, re.IGNORECASE):
            from .pipeline.document import PresentationChunkPipeline

            return PresentationChunkPipeline()
        if re.search(
            r"\.(da|wave|wav|mp3|aac|flac|ogg|aiff|au|midi|wma|"
            r"realaudio|vqf|oggvorbis|ape?)$",
            filename,
            re.IGNORECASE,
        ):
            from .pipeline.media import AudioChunkPipeline

            return AudioChunkPipeline()
        if re.search(r"\.(png|jpeg|jpg|webp|gif)$", filename, re.IGNORECASE):
            from .pipeline.image import ImageChunkPipeline

            return ImageChunkPipeline()
        if re.search(
            r"\.(mp4|mov|avi|flv|mpeg|mpg|webm|wmv|3gp|3gpp|mkv?)$",
            filename,
            re.IGNORECASE,
        ):
            from .pipeline.media import PictureVideoChunkPipeline

            return PictureVideoChunkPipeline()
        if re.search(r"\.(csv|xlsx?)$", filename, re.IGNORECASE):
            from .pipeline.excel import ExcelChunkPipeline

            return ExcelChunkPipeline()
        if re.search(
            r"\.(txt|py|js|java|c|cpp|h|php|go|ts|sh|cs|kt|sql)$",
            filename,
            re.IGNORECASE,
        ):
            from .pipeline.text import TextChunkPipeline

            return TextChunkPipeline()
        if re.search(r"\.(md|markdown)$", filename, re.IGNORECASE):
            from .pipeline.text import MarkdownChunkPipeline

            return MarkdownChunkPipeline()
        if re.search(r"\.(htm|html)$", filename, re.IGNORECASE):
            from .pipeline.text import HtmlChunkPipeline

            return HtmlChunkPipeline()
        if re.search(r"\.(json|jsonl|ldjson)$", filename, re.IGNORECASE):
            from .pipeline.text import JsonChunkPipeline

            return JsonChunkPipeline()
        if re.search(r"\.doc$", filename, re.IGNORECASE):
            from .pipeline.document import LegacyDocChunkPipeline

            return LegacyDocChunkPipeline()
        raise NotImplementedError(
            "file type not supported yet(pdf, ppt, pptx, xlsx, csv, doc, docx, "
            "txt, md, html, json supported)"
        )


__all__ = ["FileTypeRouter"]
