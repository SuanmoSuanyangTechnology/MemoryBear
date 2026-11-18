"""Deterministic source-priority dedup for QUICK/EXPRESS results.

Ported from ``api/app/core/memory/read_services/search_engine/quick_retrieval_dedup.py``.
The rule is unchanged: a MemorySummary replaces the Chunks/Statements it was
built from, and a Statement replaces its Chunk. The user-profile node is always
kept and does not consume a normal entity quota.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from src.core.adapters.retrieval.constants import USER_ENTITY_TYPE
from src.core.domain.enums import MemorySource
from src.core.domain.models import MemoryItem

SuppressionReason = Literal[
    "CHUNK_REPLACED_BY_SUMMARY",
    "STATEMENT_REPLACED_BY_SUMMARY",
    "CHUNK_REPLACED_BY_STATEMENT",
]


class SuppressionRecord(TypedDict):
    """Traceable record of one suppressed candidate."""

    node_type: str
    node_id: str
    reason: SuppressionReason
    source_chunk_id: str | None
    representative_ids: list[str]


def _source_value(memory: MemoryItem) -> str:
    source = memory.source
    return source.value if isinstance(source, MemorySource) else str(source)


def is_user_profile(memory: MemoryItem, end_user_id: str) -> bool:
    """Identify the user-profile node independently of its list position."""

    if _source_value(memory) != MemorySource.EXTRACTED_ENTITY.value:
        return False
    if end_user_id and memory.id == end_user_id:
        return True
    return memory.data.get("entity_type") == USER_ENTITY_TYPE


def _chunk_id(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value)
    return normalized or None


def _summary_chunk_ids(memory: MemoryItem) -> list[str]:
    raw_chunk_ids = memory.data.get("chunk_ids", [])
    if not isinstance(raw_chunk_ids, list):
        return []
    return [chunk_id for value in raw_chunk_ids if (chunk_id := _chunk_id(value))]


def _append_representative(
    index: dict[str, list[str]],
    chunk_id: str,
    representative_id: str,
) -> None:
    representatives = index.setdefault(chunk_id, [])
    if representative_id not in representatives:
        representatives.append(representative_id)


def filter_quick_retrieval_memories(
    memories: list[MemoryItem],
    *,
    end_user_id: str,
) -> tuple[list[MemoryItem], list[SuppressionRecord]]:
    """Suppress redundant candidates while preserving the input order."""

    summaries_by_chunk: dict[str, list[str]] = {}
    statements_by_chunk: dict[str, list[str]] = {}

    # First pass builds the source index; the representative may appear after
    # the node it suppresses.
    for memory in memories:
        source = _source_value(memory)
        if source == MemorySource.STATEMENT.value:
            chunk_id = _chunk_id(memory.data.get("chunk_id"))
            if chunk_id:
                _append_representative(statements_by_chunk, chunk_id, memory.id)
        elif source == MemorySource.MEMORY_SUMMARY.value:
            for chunk_id in _summary_chunk_ids(memory):
                _append_representative(summaries_by_chunk, chunk_id, memory.id)

    kept: list[MemoryItem] = []
    suppression_records: list[SuppressionRecord] = []

    def keep(memory: MemoryItem) -> None:
        kept.append(memory)

    def suppress(
        memory: MemoryItem,
        reason: SuppressionReason,
        *,
        source_chunk_id: str | None,
        representative_ids: list[str],
    ) -> None:
        suppression_records.append(
            {
                "node_type": _source_value(memory),
                "node_id": memory.id,
                "reason": reason,
                "source_chunk_id": source_chunk_id,
                "representative_ids": list(representative_ids),
            }
        )

    # Second pass decides strictly in input order so kept nodes and suppression
    # records stay deterministic.
    for memory in memories:
        source = _source_value(memory)
        if is_user_profile(memory, end_user_id):
            keep(memory)
        elif source == MemorySource.MEMORY_SUMMARY.value:
            keep(memory)
        elif source == MemorySource.STATEMENT.value:
            chunk_id = _chunk_id(memory.data.get("chunk_id"))
            summary_ids = summaries_by_chunk.get(chunk_id, []) if chunk_id else []
            if summary_ids:
                suppress(
                    memory,
                    "STATEMENT_REPLACED_BY_SUMMARY",
                    source_chunk_id=chunk_id,
                    representative_ids=summary_ids,
                )
            else:
                keep(memory)
        elif source == MemorySource.CHUNK.value:
            chunk_id = _chunk_id(memory.id)
            summary_ids = summaries_by_chunk.get(chunk_id, []) if chunk_id else []
            statement_ids = statements_by_chunk.get(chunk_id, []) if chunk_id else []
            if summary_ids:
                suppress(
                    memory,
                    "CHUNK_REPLACED_BY_SUMMARY",
                    source_chunk_id=chunk_id,
                    representative_ids=summary_ids,
                )
            elif statement_ids:
                suppress(
                    memory,
                    "CHUNK_REPLACED_BY_STATEMENT",
                    source_chunk_id=chunk_id,
                    representative_ids=statement_ids,
                )
            else:
                keep(memory)
        else:
            # Entities (quota applied before the cross-type ranking) and ruleless
            # families (Perceptual, Dialogue, RAG) pass through unchanged.
            keep(memory)

    return kept, suppression_records


__all__ = [
    "SuppressionReason",
    "SuppressionRecord",
    "filter_quick_retrieval_memories",
    "is_user_profile",
]