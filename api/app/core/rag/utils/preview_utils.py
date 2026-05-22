from collections import defaultdict

from app.core.rag.models.chunk import DocumentChunk, ChildDocumentChunk


def _clean_chunk_meta(chunk: dict, chunk_type: str, sort_id: int | None = None) -> dict:
    """提取 preview 所需的最小 metadata，丢弃原始 chunk 的冗余字段。"""
    meta = {"chunk_type": chunk_type}
    if sort_id is not None:
        meta["sort_id"] = sort_id
    return meta


def _build_preview_hierarchy(
    chunks: list[dict],
    chunk_mode: str = "normal",
    parent_chunks: list[dict] | None = None,
    parent_id_map: dict[int, int] | None = None,
) -> list[DocumentChunk]:
    """
    将原始分块结果转换为嵌套结构 DocumentChunk。

    Args:
        chunks: 原始分块结果（普通/QA 模式用；父子模式下传入 child_chunks）
        chunk_mode: 分块模式，取值 "normal" | "parent_child" | "qa"
        parent_chunks: 父子模式下传入父块列表
        parent_id_map: 父子模式下子块索引 → 父块索引的映射

    Returns:
        嵌套结构的 DocumentChunk 列表
    """
    if chunk_mode == "parent_child":
        if parent_chunks is None or parent_id_map is None:
            raise ValueError("parent_child mode requires parent_chunks and parent_id_map")
        return _build_parent_child_hierarchy(parent_chunks, chunks, parent_id_map)

    if chunk_mode == "qa":
        return _build_qa_hierarchy(chunks)

    return _build_normal_hierarchy(chunks)


def _build_normal_hierarchy(chunks: list[dict]) -> list[DocumentChunk]:
    """普通分块模式：每个 chunk 作为独立父块，children 为空列表。"""
    return [
        DocumentChunk(
            page_content=chunk.get("content_with_weight", ""),
            metadata=_clean_chunk_meta(chunk, chunk_type="chunk", sort_id=idx),
            children=[],
        )
        for idx, chunk in enumerate(chunks)
    ]


def _build_parent_child_hierarchy(
    parent_chunks: list[dict],
    child_chunks: list[dict],
    parent_id_map: dict[int, int],
) -> list[DocumentChunk]:
    """
    父子分块模式：将父块和子块组织为嵌套结构。

    parent_id_map 中的 key 是子块在 child_chunks 中的索引，
    value 是父块在 parent_chunks 中的索引。
    子块按 child_idx 排序，确保 children 列表顺序与父块拼接文本一致。
    """
    # 将子块按父块索引分组
    parent_to_children: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for child_idx, parent_idx in parent_id_map.items():
        if 0 <= child_idx < len(child_chunks):
            parent_to_children[parent_idx].append((child_idx, child_chunks[child_idx]))

    result = []
    for parent_idx, parent_chunk in enumerate(parent_chunks):
        children = [
            ChildDocumentChunk(
                page_content=child.get("content_with_weight", ""),
                metadata=_clean_chunk_meta(child, chunk_type="child", sort_id=child_idx),
            )
            for child_idx, child in sorted(
                parent_to_children.get(parent_idx, []),
                key=lambda x: x[0]
            )
        ]
        result.append(
            DocumentChunk(
                page_content=parent_chunk.get("content_with_weight", ""),
                metadata=_clean_chunk_meta(parent_chunk, chunk_type="parent", sort_id=parent_idx),
                children=children,
            )
        )

    return result


def _build_qa_hierarchy(chunks: list[dict]) -> list[DocumentChunk]:
    """
    QA 分块模式：每两个 chunk 组成一对 question + answer。
    偶数索引 chunk 为 question（父块），奇数索引 chunk 为 answer（子块）。
    """
    result = []
    for idx in range(0, len(chunks), 2):
        question = chunks[idx]
        answer = chunks[idx + 1] if idx + 1 < len(chunks) else None

        children = []
        if answer:
            children.append(
                ChildDocumentChunk(
                    page_content=answer.get("content_with_weight", ""),
                    metadata=_clean_chunk_meta(answer, chunk_type="qa_answer"),
                )
            )

        result.append(
            DocumentChunk(
                page_content=question.get("content_with_weight", ""),
                metadata=_clean_chunk_meta(question, chunk_type="qa_question"),
                children=children,
            )
        )
    return result
