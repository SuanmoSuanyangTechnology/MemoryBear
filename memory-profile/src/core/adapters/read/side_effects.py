"""PostgreSQL adapter for the non-fatal side effects of a memory read.

Ports the two writes the API host performs after a retrieval completes:

* the DEEP/NORMAL **short-term memo** (``memory_short_term``), whose
  ``retrieved_content`` is regrouped as ``[{query: [contents]}, ...]`` from the
  retrieved items — see
  ``api/app/core/memory/pipelines/memory_read.py::_save_short_term``;
* the **retrieval display card** (``memory_display_records``), projected by
  ``api/app/services/memory_retrieval_display_service.py::
  build_retrieve_snapshot`` and delivered fire-and-forget, exactly like
  ``MemoryRetrievalDisplayQueue``: the read never waits for PostgreSQL and a
  dropped card is acceptable.

Neither method is allowed to raise into the read: short-term failures are
logged and swallowed; display writes happen on a detached task whose failure is
logged after the response path has already moved on.

Display-text shaping is imported from the domain layer
(``src.core.domain.text``), the single implementation shared with the
application projections. The snapshot projection itself stays local to this
adapter because it is display-policy over a concrete ``ReadResult``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from src.common.timing import utcnow_naive
from src.core.domain.enums import MemorySource
from src.core.domain.models import MemoryItem, ReadResult
from src.core.domain.ports import ReadSideEffectsPort
from src.core.domain.text import (
    ENTITY_MAX_LENGTH,
    MAX_ENTITY_COUNT,
    MAX_SUMMARY_COUNT,
    SUMMARY_MAX_LENGTH,
    clean_query_for_display,
    normalize_display_text,
)
from src.repositories.memory_read_mutation import (
    MemoryReadMutationRepository,
    RetrieveDisplayRecord,
)

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

#: Only user-visible retrieval modes produce a display card; ``recent`` / ``meta``
#: are outside the host's ``RETRIEVE_SEARCH_MODES`` whitelist.
RETRIEVE_SEARCH_MODES = frozenset({"deep", "normal", "quick", "express"})

#: The host skips the short-term memo when the answer carries no real summary.
INSUFFICIENT_INFORMATION_MARKER = "信息不足"

_DISPLAY_SUMMARY_SOURCE = MemorySource.MEMORY_SUMMARY.value
_DISPLAY_ENTITY_SOURCE = MemorySource.EXTRACTED_ENTITY.value

#: Strong reference to in-flight display writes so ``asyncio`` does not garbage
#: collect a task before it runs; the done-callback removes it again.
_DISPLAY_WRITE_TASKS: set[asyncio.Task[None]] = set()


def aggregate_display_content(
    summaries: Sequence[str],
    entities: Sequence[str],
    language: str,
) -> str:
    """Join selected summaries and entity names into one card body.

    One summary is printed as-is; several are numbered per line. Entity names
    follow as a ``相关内容 / Related`` sentence. Both empty means "no card".
    """

    parts: list[str] = []

    if len(summaries) == 1:
        parts.append(summaries[0])
    elif summaries:
        parts.append("\n".join(f"{index}. {text}" for index, text in enumerate(summaries, 1)))

    if entities:
        if language == "en":
            parts.append(f"Related: {', '.join(entities)}.")
        else:
            parts.append(f"相关内容：{'、'.join(entities)}。")

    return "\n".join(parts)


def build_retrieve_snapshot(
    result: ReadResult,
    query: str,
    language: str,
) -> dict[str, str] | None:
    """Project one read result into a display snapshot (``query`` + ``content``).

    Only ``MemorySummary`` and ``ExtractedEntity`` nodes are displayable. The
    best score wins per ``(source, id)`` so a node that appears several times
    under different labels is not lost or double-counted; entities are selected
    and de-duplicated by their normalized name, with at most three of each kind.
    ``None`` means the read produced nothing worth a card.
    """

    memories = result.memories or []

    # Keep the highest score per (node family, memory id); insertion order of the
    # mapping decides ties, exactly like the host projection.
    best: dict[tuple[str, str], tuple[float, MemoryItem]] = {}
    for memory in memories:
        source = _source_value(memory.source)
        if source not in (_DISPLAY_SUMMARY_SOURCE, _DISPLAY_ENTITY_SOURCE):
            continue

        data = memory.data or {}
        memory_id = str(data.get("id") or memory.id or "").strip()
        if not memory_id:
            logger.debug(
                "retrieval_display skip node without id source=%s", source
            )
            continue

        try:
            score = float(memory.score or 0)
        except (TypeError, ValueError):
            score = 0.0

        key = (source, memory_id)
        if key not in best or score > best[key][0]:
            best[key] = (score, memory)

    scored_summaries: list[tuple[float, str]] = []
    scored_entities: list[tuple[float, str]] = []
    for score, memory in best.values():
        data = memory.data or {}
        if _source_value(memory.source) == _DISPLAY_SUMMARY_SOURCE:
            text = normalize_display_text(data.get("content"), SUMMARY_MAX_LENGTH)
            if text:
                scored_summaries.append((score, text))
        else:
            text = normalize_display_text(data.get("name"), ENTITY_MAX_LENGTH)
            if text:
                scored_entities.append((score, text))

    # Score descending, then text ascending so equal scores stay deterministic.
    scored_summaries.sort(key=lambda item: (-item[0], item[1]))
    scored_entities.sort(key=lambda item: (-item[0], item[1]))

    summaries = [text for _, text in scored_summaries[:MAX_SUMMARY_COUNT]]

    seen_entity_keys: set[str] = set()
    top_entities: list[str] = []
    for _, text in scored_entities:
        key = text.casefold()
        if key in seen_entity_keys:
            continue
        seen_entity_keys.add(key)
        top_entities.append(text)
        if len(top_entities) >= MAX_ENTITY_COUNT:
            break

    # An entity already spelled out in a selected summary adds nothing to the card.
    summary_blob = "\n".join(summaries).casefold()
    entities = [text for text in top_entities if text.casefold() not in summary_blob]

    content = aggregate_display_content(summaries, entities, language)
    if not content:
        return None
    return {"query": query, "content": content}


def _source_value(source: MemorySource | str) -> str:
    """Normalize a ``MemoryItem.source`` (enum or raw wire string) to its value."""

    if isinstance(source, MemorySource):
        return source.value
    return str(source)


class PostgresReadSideEffects:
    """Best-effort short-term memo and display-card writer."""

    def __init__(self, mutations: MemoryReadMutationRepository) -> None:
        self._mutations = mutations

    async def save_short_term(
        self,
        *,
        end_user_id: str,
        query: str,
        answer: str,
        memories: Sequence[MemoryItem],
        search_switch: str,
    ) -> None:
        """Regroup the retrieved contents and upsert the short-term memo.

        Never raises: a failed memo write only warns, so the answer still returns.
        """

        try:
            if not answer or INSUFFICIENT_INFORMATION_MARKER in answer:
                logger.debug(
                    "short_term skip empty/insufficient answer end_user=%s",
                    end_user_id,
                )
                return

            query_groups: dict[str, list[str]] = {}
            for memory in memories:
                if not memory.content:
                    continue
                memory_query = memory.query or query
                bucket = query_groups.setdefault(memory_query, [])
                if memory.content not in bucket:
                    bucket.append(memory.content)
            retrieved_content = [
                {memory_query: contents} for memory_query, contents in query_groups.items()
            ]

            await self._mutations.upsert_short_term(
                end_user_id=end_user_id,
                messages=query,
                aimessages=answer,
                search_switch=search_switch,
                retrieved_content=retrieved_content,
            )
        except Exception:
            logger.warning(
                "short_term upsert failed (read unaffected) end_user=%s",
                end_user_id,
                exc_info=True,
            )

    async def enqueue_display(
        self,
        *,
        end_user_id: str,
        workspace_id: UUID | None,
        language: str,
        search_mode: str,
        query: str,
        result: ReadResult,
    ) -> bool:
        """Schedule one display-card write and return immediately.

        Returns ``False`` when nothing was scheduled: a mode outside the display
        whitelist, a non-UUID host, or a read without displayable nodes. The card
        is written on a detached task; a failure there is only logged, matching
        the host queue's drop-on-failure semantics.
        """

        try:
            if search_mode not in RETRIEVE_SEARCH_MODES:
                logger.debug(
                    "retrieval_display skip mode outside whitelist mode=%s", search_mode
                )
                return False

            try:
                end_user_uuid = UUID(str(end_user_id))
            except (AttributeError, TypeError, ValueError):
                logger.warning(
                    "retrieval_display skip invalid end_user_id end_user=%s", end_user_id
                )
                return False

            snapshot = build_retrieve_snapshot(
                result=result,
                query=clean_query_for_display(query),
                language=language,
            )
            if snapshot is None:
                logger.debug(
                    "retrieval_display skip read without displayable nodes end_user=%s",
                    end_user_id,
                )
                return False

            record = RetrieveDisplayRecord(
                id=uuid4(),
                end_user_id=end_user_uuid,
                workspace_id=workspace_id,
                operation_id=uuid4(),
                search_mode=search_mode,
                query=snapshot["query"],
                content=snapshot["content"],
                occurred_at=utcnow_naive(),
            )
            try:
                task = asyncio.create_task(self._write_display_card(record))
            except RuntimeError:
                logger.warning(
                    "retrieval_display dropped (no running event loop) end_user=%s",
                    end_user_id,
                )
                return False

            _DISPLAY_WRITE_TASKS.add(task)
            task.add_done_callback(_DISPLAY_WRITE_TASKS.discard)
            return True
        except Exception:
            logger.warning(
                "retrieval_display enqueue failed (read unaffected) end_user=%s",
                end_user_id,
                exc_info=True,
            )
            return False

    async def _write_display_card(self, record: RetrieveDisplayRecord) -> None:
        """Write one card; called on a detached task and never awaited by a read."""

        try:
            inserted = await self._mutations.insert_display_records([record])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "retrieval_display write failed (card dropped) end_user=%s",
                record.end_user_id,
                exc_info=True,
            )
        else:
            logger.debug(
                "retrieval_display write done inserted=%s end_user=%s",
                inserted,
                record.end_user_id,
            )


def build_side_effects(runtime: ProfileRuntime) -> ReadSideEffectsPort:
    """Composition entry point for the read side-effect port."""

    return PostgresReadSideEffects(MemoryReadMutationRepository(runtime.postgres))


async def flush_pending_display_writes() -> None:
    """Await currently in-flight display writes (tests and graceful shutdown only).

    The read path never calls this; it exists so a process shutdown or a test can
    give already-scheduled cards a chance to land.
    """

    pending = list(_DISPLAY_WRITE_TASKS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


__all__ = [
    "INSUFFICIENT_INFORMATION_MARKER",
    "PostgresReadSideEffects",
    "RETRIEVE_SEARCH_MODES",
    "aggregate_display_content",
    "build_retrieve_snapshot",
    "build_side_effects",
    "flush_pending_display_writes",
]