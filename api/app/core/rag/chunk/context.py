import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from app.core.rag.nlp import rag_tokenizer


DEFAULT_PARSER_CONFIG = {
    "layout_recognize": "DeepDOC",
    "chunk_token_num": 512,
    "delimiter": "\n!?。；！？",
    "analyze_hyperlink": True,
}


class ChunkOutputMode(str, Enum):
    NORMAL = "normal"
    QA = "qa"
    PARENT_CHILD = "parent_child"


@dataclass
class ChunkContext:
    filename: str
    binary: bytes | None
    from_page: int
    to_page: int
    lang: str
    callback: Callable | None
    vision_model: Any
    kwargs: dict
    parser_config: dict
    doc: dict
    is_english: bool
    is_root: bool
    chunk_output_mode: ChunkOutputMode


@dataclass
class ParseResult:
    sections: list | None = None
    tables: list | None = None
    pdf_parser: Any = None
    section_images: list | None = None
    urls: set | None = None
    direct_result: list | None = None
    merge_strategy: str = "naive"
    url_res: list | None = None
    append_embed: bool = True


@dataclass
class MergeResult:
    chunks: list
    images: list | None = None


def build_chunk_doc(filename: str) -> dict:
    doc = {
        "docnm_kwd": filename,
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename)),
    }
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    return doc


def build_chunk_context(
    filename,
    binary=None,
    from_page=0,
    to_page=100000,
    lang="Chinese",
    callback=None,
    vision_model=None,
    **kwargs,
) -> ChunkContext:
    parser_config = kwargs.get("parser_config", DEFAULT_PARSER_CONFIG.copy())
    explicit_output_mode = kwargs.get("chunk_output_mode")
    raw_output_mode = explicit_output_mode or (
        ChunkOutputMode.PARENT_CHILD if parser_config.get("parent_child_mode", False) else ChunkOutputMode.NORMAL
    )
    chunk_output_mode = ChunkOutputMode(raw_output_mode)
    return ChunkContext(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        kwargs=kwargs,
        parser_config=parser_config,
        doc=build_chunk_doc(filename),
        is_english=lang.lower() == "english",
        is_root=kwargs.get("is_root", True),
        chunk_output_mode=chunk_output_mode,
    )
