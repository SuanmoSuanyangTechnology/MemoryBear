import io
import os
import re
import tempfile

import numpy as np
from PIL import Image

from app.core.rag.deepdoc.vision import OCR
from app.core.rag.chunk.context import ChunkContext, ParseResult
from app.core.rag.nlp import rag_tokenizer, tokenize

from .base import ChunkPipeline

AUDIO_EXTS = [
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
]

VIDEO_EXTS = [".mp4", ".mov", ".avi", ".flv", ".mpeg", ".mpg", ".webm", ".wmv", ".3gp", ".3gpp", ".mkv"]

MEDIA_OCR = OCR()


class AudioChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        binary = ctx.binary
        if not binary:
            with open(ctx.filename, "rb") as file:
                binary = file.read()

        doc = {
            "docnm_kwd": ctx.filename,
            "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", ctx.filename)),
        }
        doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])

        temp_path = ""
        try:
            _, extension = os.path.splitext(ctx.filename)
            if not extension:
                raise RuntimeError("No extension detected.")

            if extension not in AUDIO_EXTS:
                raise RuntimeError(f"Extension {extension} is not supported yet.")

            with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as temp_file:
                temp_file.write(binary)
                temp_file.flush()
                temp_path = os.path.abspath(temp_file.name)

            ctx.callback(0.1, "USE Sequence2Txt LLM to transcription the audio")
            transcription, _ = ctx.vision_model.transcription(temp_path)
            ctx.callback(0.8, "Sequence2Txt LLM respond: %s ..." % transcription[:32])

            tokenize(doc, transcription, ctx.is_english)
            result = [doc]
        except Exception as error:
            ctx.callback(prog=-1, msg=str(error))
            result = []
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

        return ParseResult(
            direct_result=result,
            append_embed=False,
        )


class PictureVideoChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        binary = ctx.binary
        if not binary:
            with open(ctx.filename, "rb") as file:
                binary = file.read()

        doc = {
            "docnm_kwd": ctx.filename,
            "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", ctx.filename)),
        }

        result = []
        if any(ctx.filename.lower().endswith(extension) for extension in VIDEO_EXTS):
            try:
                doc.update({"doc_type_kwd": "video"})
                answer, _ = ctx.vision_model.chat(
                    system="",
                    history=[],
                    gen_conf={},
                    video_bytes=binary,
                    filename=ctx.filename,
                )
                ctx.callback(0.8, "CV LLM respond: %s ..." % answer[:32])
                tokenize(doc, answer, ctx.is_english)
                result = [doc]
            except Exception as error:
                ctx.callback(prog=-1, msg=str(error))
        else:
            image = Image.open(io.BytesIO(binary)).convert("RGB")
            doc.update(
                {
                    "image": image,
                    "doc_type_kwd": "image",
                }
            )
            boxes = MEDIA_OCR(np.array(image))
            text = "\n".join([box_text[0] for _, box_text in boxes if box_text[0]])
            ctx.callback(0.4, "Finish OCR: (%s ...)" % text[:12])
            if (ctx.is_english and len(text.split()) > 32) or len(text) > 32:
                tokenize(doc, text, ctx.is_english)
                ctx.callback(0.8, "OCR results is too long to use CV LLM.")
                result = [doc]
            else:
                try:
                    ctx.callback(0.4, "Use CV LLM to describe the picture.")
                    image_binary = io.BytesIO()
                    image.save(image_binary, format="JPEG")
                    image_binary.seek(0)
                    answer, _ = ctx.vision_model.describe(image_binary.read())
                    ctx.callback(0.8, "CV LLM respond: %s ..." % answer[:32])
                    text += "\n" + answer
                    tokenize(doc, text, ctx.is_english)
                    result = [doc]
                except Exception as error:
                    ctx.callback(prog=-1, msg=str(error))

        return ParseResult(
            direct_result=result,
            append_embed=False,
        )
