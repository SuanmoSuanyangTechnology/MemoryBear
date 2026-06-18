from copy import deepcopy
from typing import Callable

from app.core.rag.chunk.context import ChunkOutputMode, LogicalChunk, LogicalChunkType


FULL_DOC_MAX_CHARS = 10000


def truncate_to_chars(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars]


def build_parent_child_logical_chunks(
    parent_chunks: list[LogicalChunk],
    parser_config: dict,
    split_text: Callable[[str, int], list[str]],
) -> tuple[list[LogicalChunk], list[LogicalChunk], dict[int, int]]:
    parent_chunk_mode = parser_config.get("parent_chunk_mode", "paragraph")

    if parent_chunk_mode == "full-doc":
        full_text = "\n\n".join(str(chunk.content) for chunk in parent_chunks if chunk.type == LogicalChunkType.TEXT)
        truncated = truncate_to_chars(full_text, FULL_DOC_MAX_CHARS)
        full_parent = LogicalChunk(type=LogicalChunkType.TEXT, content=truncated)
        child_chunks: list[LogicalChunk] = []
        parent_id_map: dict[int, int] = {}
        child_token_num = int(parser_config.get("chunk_token_num", 128))
        for child_text in split_text(full_text, child_token_num):
            if not child_text.strip():
                continue
            parent_id_map[len(child_chunks)] = 0
            child_chunks.append(LogicalChunk(type=LogicalChunkType.TEXT, content=child_text))
        return child_chunks, [full_parent], parent_id_map

    child_token_num = int(parser_config.get("chunk_token_num", 128))
    child_chunks: list[LogicalChunk] = []
    parent_id_map: dict[int, int] = {}

    for parent_index, parent_chunk in enumerate(parent_chunks):
        if parent_chunk.type is LogicalChunkType.TEXT:
            child_texts = split_text(str(parent_chunk.content), child_token_num)
            image_attached = False
            for child_text in child_texts:
                if not child_text.strip():
                    continue
                child = LogicalChunk(
                    type=LogicalChunkType.TEXT,
                    content=child_text,
                    image=parent_chunk.image if not image_attached else None,
                    positions=deepcopy(parent_chunk.positions),
                    metadata=deepcopy(parent_chunk.metadata),
                )
                image_attached = image_attached or parent_chunk.image is not None
                parent_id_map[len(child_chunks)] = parent_index
                child_chunks.append(child)
            continue

        child = deepcopy(parent_chunk)
        parent_id_map[len(child_chunks)] = parent_index
        child_chunks.append(child)

    return child_chunks, parent_chunks, parent_id_map


def append_external_parent_child_chunks(
    child_res: list[dict],
    parent_res: list[dict],
    parent_id_map: dict[int, int],
    external_chunks: list[dict],
) -> tuple[list[dict], list[dict], dict[int, int]]:
    for external_chunk in external_chunks:
        child_index = len(child_res)
        parent_index = len(parent_res)
        content = external_chunk.get("content_with_weight", "")
        parent_res.append({
            "content_with_weight": content,
            "image": external_chunk.get("image"),
        })
        child_res.append(external_chunk)
        parent_id_map[child_index] = parent_index
    return child_res, parent_res, parent_id_map


def build_atomic_parent_child_chunks(chunks: list[dict]) -> tuple[list[dict], list[dict], dict[int, int]]:
    parent_res: list[dict] = []
    parent_id_map: dict[int, int] = {}
    for index, chunk in enumerate(chunks):
        parent_res.append({
            "content_with_weight": chunk.get("content_with_weight", ""),
            "image": chunk.get("image"),
        })
        parent_id_map[index] = index
    return chunks, parent_res, parent_id_map


def chunk_parent_child_pipeline(
    filename,
    binary=None,
    from_page=0,
    to_page=100000,
    lang="Chinese",
    callback=None,
    vision_model=None,
    **kwargs,
):
    from app.core.rag.chunk import chunk_pipeline

    wrapper_kwargs = dict(kwargs)
    wrapper_kwargs["chunk_output_mode"] = ChunkOutputMode.PARENT_CHILD
    return chunk_pipeline(
        filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        **wrapper_kwargs,
    )
