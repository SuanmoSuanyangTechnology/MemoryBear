import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from app.core.rag.nlp import rag_tokenizer


DEFAULT_PARSER_CONFIG = {
    "layout_recognize": "DeepDOC",
    "chunk_token_num": 512,
    "delimiter": "\n!?。；！？",
    "analyze_hyperlink": True,
    "image_vision_enabled": True,
}


def is_image_vision_enabled(parser_config: dict | None) -> bool:
    raw_value = (parser_config or {}).get("image_vision_enabled", True)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw_value)


class ChunkOutputMode(str, Enum):
    NORMAL = "normal"
    QA = "qa"
    PARENT_CHILD = "parent_child"


class LogicalChunkType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"


class ParsedBlockType(str, Enum):
    HEADING = "heading"
    TEXT = "text"
    LIST = "list"
    BLOCKQUOTE = "blockquote"
    CODE = "code"
    TABLE = "table"
    IMAGE = "image"


@dataclass
class ParsedBlock:
    type: ParsedBlockType
    content: Any = ""
    raw: str = ""
    seq: int = 0
    start_line: int | None = None
    end_line: int | None = None
    image: Any = None
    positions: list | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LogicalChunk:
    type: LogicalChunkType
    content: Any = ""
    image: Any = None
    positions: list | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
    blocks: list[ParsedBlock] | None = None
    markdown_preprocess_profile: str | None = None


@dataclass
class MergeResult:
    chunks: list = field(default_factory=list)
    images: list | None = None
    logical_chunks: list[LogicalChunk] | None = None
    parent_chunks: list[LogicalChunk] | None = None
    child_chunks: list[LogicalChunk] | None = None
    parent_id_map: dict[int, int] | None = None
    pdf_parser: Any = None


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
