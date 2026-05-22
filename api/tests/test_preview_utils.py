from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.utils.preview_utils import _build_preview_hierarchy


def test_build_normal_hierarchy():
    """普通模式下每个 chunk 作为独立父块，children 为空列表"""
    chunks = [
        {"content_with_weight": "chunk1 text", "doc_id": "doc1"},
        {"content_with_weight": "chunk2 text", "doc_id": "doc2"},
    ]
    result = _build_preview_hierarchy(chunks, chunk_mode="normal")

    assert len(result) == 2
    assert result[0].page_content == "chunk1 text"
    assert result[0].metadata["doc_id"] == "doc1"
    assert result[0].children == []
    assert result[1].page_content == "chunk2 text"


def test_build_parent_child_hierarchy():
    """父子模式下子块应嵌套在对应父块下"""
    parent_chunks = [
        {"content_with_weight": "parent 1 text", "doc_id": "p1"},
        {"content_with_weight": "parent 2 text", "doc_id": "p2"},
    ]
    child_chunks = [
        {"content_with_weight": "child 1.1 text", "doc_id": "c1"},
        {"content_with_weight": "child 1.2 text", "doc_id": "c2"},
        {"content_with_weight": "child 2.1 text", "doc_id": "c3"},
    ]

    # parent_id_map: child_idx -> parent_idx
    parent_id_map = {
        0: 0,   # child 1.1 -> parent 1
        1: 0,   # child 1.2 -> parent 1
        2: 1,   # child 2.1 -> parent 2
    }

    result = _build_preview_hierarchy(
        child_chunks, chunk_mode="parent_child",
        parent_chunks=parent_chunks, parent_id_map=parent_id_map
    )

    assert len(result) == 2
    assert result[0].page_content == "parent 1 text"
    assert result[0].metadata["chunk_type"] == "parent"
    assert len(result[0].children) == 2
    assert result[0].children[0].page_content == "child 1.1 text"
    assert result[0].children[1].page_content == "child 1.2 text"

    assert result[1].page_content == "parent 2 text"
    assert result[1].metadata["chunk_type"] == "parent"
    assert len(result[1].children) == 1
    assert result[1].children[0].page_content == "child 2.1 text"


def test_build_parent_child_hierarchy_empty_children():
    """父子模式下某个父块没有子块"""
    parent_chunks = [
        {"content_with_weight": "parent 1 text", "doc_id": "p1"},
        {"content_with_weight": "parent 2 text", "doc_id": "p2"},
    ]
    child_chunks = [
        {"content_with_weight": "child 1.1 text", "doc_id": "c1"},
    ]

    parent_id_map = {
        0: 0,   # child 1.1 -> parent 1
    }

    result = _build_preview_hierarchy(
        child_chunks, chunk_mode="parent_child",
        parent_chunks=parent_chunks, parent_id_map=parent_id_map
    )

    assert len(result) == 2
    assert len(result[0].children) == 1
    assert len(result[1].children) == 0


def test_build_qa_hierarchy():
    """QA 模式下每两个 chunk 组成一对 question + answer"""
    chunks = [
        {"content_with_weight": "Q1", "doc_id": "q1"},
        {"content_with_weight": "A1", "doc_id": "a1"},
        {"content_with_weight": "Q2", "doc_id": "q2"},
        {"content_with_weight": "A2", "doc_id": "a2"},
    ]
    result = _build_preview_hierarchy(chunks, chunk_mode="qa")

    assert len(result) == 2
    assert result[0].page_content == "Q1"
    assert result[0].metadata["chunk_type"] == "qa_question"
    assert len(result[0].children) == 1
    assert result[0].children[0].page_content == "A1"

    assert result[1].page_content == "Q2"
    assert result[1].metadata["chunk_type"] == "qa_question"
    assert len(result[1].children) == 1
    assert result[1].children[0].page_content == "A2"


def test_build_qa_hierarchy_odd_count():
    """QA 模式下 chunk 数量为奇数时，最后一个 question 没有 answer"""
    chunks = [
        {"content_with_weight": "Q1", "doc_id": "q1"},
        {"content_with_weight": "A1", "doc_id": "a1"},
        {"content_with_weight": "Q2", "doc_id": "q2"},
    ]
    result = _build_preview_hierarchy(chunks, chunk_mode="qa")

    assert len(result) == 2
    assert result[1].page_content == "Q2"
    assert result[1].children == []


def test_build_parent_child_hierarchy_missing_params():
    """父子模式下缺少 parent_chunks 或 parent_id_map 应报错"""
    try:
        _build_preview_hierarchy([], chunk_mode="parent_child")
        assert False, "应该抛出 ValueError"
    except ValueError as e:
        assert "parent_chunks" in str(e) or "parent_id_map" in str(e)
