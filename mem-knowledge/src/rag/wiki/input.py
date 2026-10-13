"""Read the existing source chunks and retain deterministic evidence handles."""

from __future__ import annotations

from ..chunk.token_utils import split_by_token_limit
from ..models.chunk import chunk_retrieval_content
from ..retrieval.async_elasticsearch import AsyncChunkStore
from ..vdb.field import Field
from .config import WikiError, fingerprint
from .model import tokens


def normalize_chunks(chunks):
    by_id = {str(c.metadata.get("doc_id") or c.metadata.get("chunk_id")): c for c in chunks}
    sources, seen = [], set()
    parents = {key: c for key, c in by_id.items() if c.metadata.get("chunk_type") == "parent"}
    for chunk_id, chunk in by_id.items():
        meta = chunk.metadata
        if chunk_id in {"None", ""}:
            raise WikiError("WIKI_SOURCE_ID_MISSING", retryable=False)
        if meta.get("unit_kind") not in {None, "chunk_record"}:
            continue
        if meta.get("chunk_type") == "qa" and meta.get("source_chunk_id") in by_id:
            # Automatically derived QA is a retrieval aid, not an independent fact.
            continue
        parent = parents.get(str(meta.get("parent_id")))
        text = chunk_retrieval_content(chunk).strip()
        if meta.get("chunk_type") == "qa":
            text = f"Question: {meta.get('question', '')}\nAnswer: {meta.get('answer', '')}".strip()
        if parent and text and text in parent.page_content:
            continue
        if not text:
            continue
        identity = (text, str(meta.get("parent_id") or chunk_id))
        if identity in seen:
            continue
        seen.add(identity)
        sources.append(
            {"text": text, "chunk_id": chunk_id, "heading": str(meta.get("heading") or "")}
        )
    return sources


async def read_sources(
    runtime, kb_id, document_id, *, expected_ids=None, exact=False, expect_index=False
):
    client = await runtime.elasticsearch.client()
    store = AsyncChunkStore(client, kb_id)
    if not await client.indices.exists(index=store.index):
        if expected_ids or expect_index:
            raise WikiError("WIKI_SOURCE_NOT_VISIBLE")
        return []
    response = await client.indices.refresh(index=store.index)
    if response.get("_shards", {}).get("failed", 0):
        raise WikiError("WIKI_SOURCE_NOT_VISIBLE")
    fields = [
        Field.CONTENT_KEY.value,
        Field.METADATA_KEY.value,
        Field.CHUNK_TYPE.value,
        Field.QUESTION.value,
        Field.ANSWER.value,
        Field.UNIT_KIND.value,
        Field.CHUNK_ID.value,
    ]
    chunks = []
    async for hit in store.iter_by_segment(
        document_id=str(document_id), asc=True, source_includes=fields
    ):
        stored = hit.get("_source") or {}
        if stored.get("unit_kind") not in (None, "chunk_record"):
            continue
        chunk = store.hit_to_chunk(hit)
        if stored.get("chunk_id"):
            chunk.metadata["doc_id"] = stored["chunk_id"]
        chunks.append(chunk)
    if expected_ids is not None:
        actual_ids = {str(chunk.metadata.get("doc_id")) for chunk in chunks}
        expected = set(expected_ids)
        if not expected.issubset(actual_ids) or (exact and actual_ids != expected):
            raise WikiError("WIKI_SOURCE_INCOMPLETE")
    return normalize_chunks(chunks)


def segment_sources(sources, settings):
    # Reserve space for prompts, schemas and confirmed topic handles.
    budget = max(128, settings.kb_wiki_input_tokens // 3)
    pieces = []
    for source in sources:
        for text in split_by_token_limit(source["text"], budget):
            pieces.append({**source, "ref": f"S{len(pieces) + 1}", "text": text})
    segments, current = [], []
    for item in pieces:
        if current and tokens([*current, item]) > budget:
            segments.append(current)
            current = []
        current.append(item)
    if current:
        segments.append(current)
    if len(segments) > settings.kb_wiki_max_segments:
        raise WikiError("WIKI_SOURCE_BUDGET_EXCEEDED", retryable=False)
    return segments, {item["ref"]: item for item in pieces}, fingerprint(sources)
