"""Audio and video pipelines without any OCR or DeepDoc branch."""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

from ...models.media import QWenCV, QWenSeq2txt
from ..context import ChunkContext, ParseResult
from ..tokenization import tokenize
from .base import ChunkPipeline

AUDIO_EXTS = {
    ".da",
    ".wave",
    ".wav",
    ".mp3",
    ".aac",
    ".flac",
    ".ogg",
    ".aiff",
    ".au",
    ".midi",
    ".wma",
    ".realaudio",
    ".vqf",
    ".oggvorbis",
    ".ape",
}
VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".avi",
    ".flv",
    ".mpeg",
    ".mpg",
    ".webm",
    ".wmv",
    ".3gp",
    ".3gpp",
    ".mkv",
}


class AudioChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        binary = ctx.binary if ctx.binary is not None else Path(ctx.filename).read_bytes()
        extension = Path(ctx.filename).suffix.lower()
        if extension not in AUDIO_EXTS:
            raise RuntimeError(f"Extension {extension} is not supported yet.")
        temporary_path = ""
        result = []
        try:
            with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as temporary:
                temporary.write(binary)
                temporary_path = temporary.name
            model = ctx.vision_model or QWenSeq2txt(lang=ctx.lang)
            self._callback(ctx, 0.1, "Use media model to transcribe audio.")
            transcription, _tokens = model.transcription(temporary_path)
            document = copy.deepcopy(ctx.doc)
            tokenize(document, transcription, ctx.is_english)
            result = [document]
            self._callback(ctx, 0.8, "Finish audio transcription.")
        except Exception as exc:  # noqa: BLE001 - preserve legacy empty-result media behavior.
            self._callback(ctx, -1, f"Audio transcription failed: {type(exc).__name__}")
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)
        return ParseResult(direct_result=result, append_embed=False)

    @staticmethod
    def _callback(ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


class PictureVideoChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        binary = ctx.binary if ctx.binary is not None else Path(ctx.filename).read_bytes()
        extension = Path(ctx.filename).suffix.lower()
        if extension not in VIDEO_EXTS:
            raise RuntimeError(f"Extension {extension} is not supported yet.")
        result = []
        try:
            model = ctx.vision_model or QWenCV(lang=ctx.lang)
            answer, _tokens = model.chat(
                system="",
                history=[],
                gen_conf={},
                video_bytes=binary,
                filename=ctx.filename,
            )
            document = copy.deepcopy(ctx.doc)
            document["doc_type_kwd"] = "video"
            tokenize(document, answer, ctx.is_english)
            result = [document]
            self._callback(ctx, 0.8, "Finish video transcription.")
        except Exception as exc:  # noqa: BLE001 - preserve legacy empty-result media behavior.
            self._callback(ctx, -1, f"Video transcription failed: {type(exc).__name__}")
        return ParseResult(direct_result=result, append_embed=False)

    @staticmethod
    def _callback(ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


__all__ = ["AudioChunkPipeline", "PictureVideoChunkPipeline"]
