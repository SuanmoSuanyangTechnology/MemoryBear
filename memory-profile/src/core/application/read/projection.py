"""Public projections of retrieval results.

Ported from the host's ``retrieval_trace/stage_projection.py`` and
``memory_validation_service.py``. These functions are the public display
contract: memory-item ranks/types/content, the profile block, relation items,
the validation trace and the legacy ``intermediate_outputs`` list.

They depend only on domain models and never on SSE, persistence or LangChain.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from src.core.domain.enums import MemoryDisplayType, MemorySource, SearchStrategy
from src.core.domain.models import (
    CommunityRetrievalTrace,
    MemoryItem,
    ReadResult,
    RelationItem,
    RetrievalExecutionTrace,
    build_score_trace,
    finite_or_none,
)
from src.core.domain.profile import (
    LEGACY_METADATA_FIELDS,
    PROFILE_DISPLAY_KEYS,
    is_user_profile,
    project_display_profile,
)

_PROFILE_FIELDS = (
    "aliases_name",
    "description",
    *PROFILE_DISPLAY_KEYS,
)
_ARRAY_PROFILE_FIELDS = tuple(field for field in _PROFILE_FIELDS if field != "description")
_TIME_PREFIX = re.compile(r"^\[\d{4}-\d{2}-\d{2}T[^]]+\]\s*")

_COMMUNITY_STAGES = (
    "community_searched",
    "community_scenes_searched",
    "community_evidence_packed",
)


def _source_value(source: MemorySource | str) -> str:
    return source.value if isinstance(source, MemorySource) else str(source)


def display_text(value: Any, limit: int) -> str:
    """Remove only the documented timestamp prefix and truncate text."""

    if value is None:
        return ""
    text = str(value).strip()
    text = _TIME_PREFIX.sub("", text, count=1)
    return text[:limit]


def _string_value(value: Any, limit: int) -> str:
    if isinstance(value, dict):
        for key in ("content", "name", "description", "target"):
            candidate = display_text(value.get(key), limit)
            if candidate:
                return candidate
        return ""
    return display_text(value, limit)


def _profile_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    values: list[str] = []
    for item in list(value)[:5]:
        text = _string_value(item, 200)
        if text:
            values.append(text)
    return values


def project_profile_data(result: ReadResult | None) -> dict[str, Any]:
    """Return the stable profile shape, including empty fields."""

    source = result.memories[0].data if result and result.memories else {}
    source = source if isinstance(source, dict) else {}
    profile: dict[str, Any] = {
        "aliases_name": _profile_list(
            source.get("aliases_name", source.get("aliases"))
        ),
        "description": _string_value(source.get("description"), 200),
    }
    display_profile = project_display_profile(source)
    for field in _ARRAY_PROFILE_FIELDS:
        if field == "aliases_name":
            continue
        profile[field] = _profile_list(display_profile.get(field))
    return profile


def profile_has_content(profile: dict[str, Any]) -> bool:
    return any(bool(value) for value in profile.values())


def memory_type(source: MemorySource | str) -> MemoryDisplayType:
    value = _source_value(source)
    if value == "memory_l0":
        return MemoryDisplayType.PROFILE
    return {
        MemorySource.STATEMENT.value: MemoryDisplayType.FACT,
        MemorySource.CHUNK.value: MemoryDisplayType.FACT,
        MemorySource.DIALOGUE.value: MemoryDisplayType.FACT,
        MemorySource.EXTRACTED_ENTITY.value: MemoryDisplayType.ENTITY,
        MemorySource.MEMORY_SUMMARY.value: MemoryDisplayType.SUMMARY,
        MemorySource.PERCEPTUAL.value: MemoryDisplayType.FILE,
        MemorySource.RAG.value: MemoryDisplayType.FACT,
        MemorySource.RAG_MEMORY.value: MemoryDisplayType.FACT,
    }.get(value, MemoryDisplayType.UNKNOWN)


def _score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(score, 4) if math.isfinite(score) else 0.0


def _perceptual_type(value: Any) -> int | None:
    """Normalize the public perceptual type; never leak unexpected source data."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped.isdigit():
            return None
        normalized = int(stripped)
    else:
        return None
    return normalized if normalized in {1, 2, 3} else None


def sanitize_relevance(value: Any) -> float:
    """Backward-compatible legacy helper; new payloads use ``score``."""

    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return round(min(max(score, 0.0), 1.0), 4)


def _memory_content(
    memory: MemoryItem,
    kind: MemoryDisplayType,
    *,
    prefer_perceptual_display: bool = False,
) -> str:
    data = memory.data if isinstance(memory.data, dict) else {}
    if kind == MemoryDisplayType.PROFILE:
        profile = project_profile_data(ReadResult(memories=[memory]))
        values = (
            profile["aliases_name"]
            or ([profile["description"]] if profile["description"] else [])
        )
        if not values:
            values = next(
                (profile[field] for field in PROFILE_DISPLAY_KEYS if profile[field]), []
            )
        return display_text("；".join(values), 300)
    if kind == MemoryDisplayType.ENTITY:
        candidates = (
            data.get("description"),
            data.get("description_summary"),
            data.get("name"),
            memory.content,
        )
    elif _source_value(memory.source) == MemorySource.PERCEPTUAL.value and (
        prefer_perceptual_display
    ):
        candidates = (data.get("_perceptual_display_content"), data.get("summary"))
    elif _source_value(memory.source) == MemorySource.PERCEPTUAL.value:
        candidates = (memory.content, data.get("summary"))
    elif _source_value(memory.source) in {
        MemorySource.RAG.value,
        MemorySource.RAG_MEMORY.value,
    }:
        candidates = (memory.content,)
    else:
        candidates = (data.get("content"), memory.content)
    for candidate in candidates:
        text = _string_value(candidate, 300)
        if text:
            return text
    return ""


def _file_block(memory: MemoryItem) -> dict[str, Any]:
    data = memory.data if isinstance(memory.data, dict) else {}
    return {
        "file_name": display_text(data.get("file_name"), 1000),
        "file_path": display_text(data.get("file_path"), 2000),
        "file_type": display_text(data.get("file_type"), 200),
        # Only the protocol types 1/2/3 are exposed; anything else becomes null.
        "perceptual_type": _perceptual_type(data.get("perceptual_type")),
    }


def project_memory_item(
    memory: MemoryItem,
    rank: int,
    *,
    kind: MemoryDisplayType | None = None,
    prefer_perceptual_display: bool = False,
) -> dict[str, Any]:
    resolved_kind = kind or memory_type(memory.source)
    item: dict[str, Any] = {
        "rank": rank,
        "memory_type": resolved_kind.value,
        "source": _source_value(memory.source),
        "score": 1.0 if resolved_kind == MemoryDisplayType.PROFILE else _score(memory.score),
        "content": _memory_content(
            memory,
            resolved_kind,
            prefer_perceptual_display=prefer_perceptual_display,
        ),
    }
    if (
        resolved_kind == MemoryDisplayType.FILE
        and _source_value(memory.source) == MemorySource.PERCEPTUAL.value
    ):
        file_data = _file_block(memory)
        if any(value not in (None, "") for value in file_data.values()):
            item["file"] = file_data
    return item


def project_memory_items(
    memories: Iterable[MemoryItem], limit: int = 3
) -> list[dict[str, Any]]:
    return [
        project_memory_item(memory, index)
        for index, memory in enumerate(list(memories)[: max(limit, 0)], start=1)
    ]


def project_relation_items(
    relations: Iterable[RelationItem], limit: int = 3
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for relation in list(relations)[: max(limit, 0)]:
        items.append(
            {
                "source": display_text(relation.source, 10_000),
                "relation": display_text(relation.relation, 10_000),
                "target": display_text(relation.target, 10_000),
                "target_desc": display_text(relation.target_desc, 200),
            }
        )
    return items


def project_result_items(
    profile_result: ReadResult | None,
    search_result: ReadResult | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build the final authoritative list, non-empty profile first."""

    items: list[dict[str, Any]] = []
    profile = project_profile_data(profile_result)
    if profile_has_content(profile) and limit > 0:
        profile_memory = (
            profile_result.memories[0] if profile_result and profile_result.memories else None
        )
        if profile_memory:
            items.append(
                project_memory_item(
                    profile_memory,
                    1,
                    kind=MemoryDisplayType.PROFILE,
                )
            )
    if search_result:
        for memory in search_result.memories:
            if len(items) >= limit:
                break
            items.append(
                project_memory_item(
                    memory,
                    len(items) + 1,
                    prefer_perceptual_display=True,
                )
            )
    for index, item in enumerate(items, start=1):
        item["rank"] = index
    return items


def project_retrieval_candidate(memory: MemoryItem, rank: int) -> dict[str, Any]:
    """Project one candidate with its score trace for the validation trace event."""

    source = _source_value(memory.source)
    trace = memory.retrieval_trace
    if trace is None:
        backend = "rag" if source == MemorySource.RAG.value else "neo4j"
        trace = build_score_trace(
            node_id=memory.id,
            node_type=source,
            final_score=memory.score,
            rank_basis="provider_score" if backend == "rag" else "input_order",
            backend=backend,
            matched_queries=[memory.query],
        )
    final_score = finite_or_none(trace.final_score)
    if final_score is None:
        final_score = 0.0
    resolved_kind = memory_type(memory.source)
    item: dict[str, Any] = {
        "rank": rank,
        "memory_id": str(memory.id),
        "memory_type": resolved_kind.value,
        "source": source,
        "content": _memory_content(
            memory,
            resolved_kind,
            prefer_perceptual_display=True,
        ),
        "retrieval_type": trace.retrieval_type,
        "keyword_score": finite_or_none(trace.keyword_score),
        "semantic_score": finite_or_none(trace.semantic_score),
        "fusion_score": finite_or_none(trace.fusion_score),
        "rerank_score": finite_or_none(trace.rerank_score),
        "final_score": final_score,
        "rank_basis": trace.rank_basis,
        "hit_source": {
            "backend": trace.backend,
            "node_type": trace.node_type,
            "node_id": trace.node_id,
            "matched_queries": list(trace.matched_queries),
        },
    }
    if source == MemorySource.PERCEPTUAL.value:
        file_data = _file_block(memory)
        if any(value not in (None, "") for value in file_data.values()):
            item["file"] = file_data
    return item


def _find_stage(
    stages: Iterable[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    return next((stage for stage in stages if stage.get("stage") == name), None)


def _search_status(trace: RetrievalExecutionTrace) -> str:
    active = [trace.keyword_status]
    if trace.semantic_status != "skipped":
        active.append(trace.semantic_status)
    if active and all(status == "failed" for status in active):
        return "failed"
    return "completed"


def build_validation_trace(
    *,
    request_id: str,
    query: str,
    search_switch: str,
    end_user_id: str,
    result: ReadResult,
    collected_stages: Iterable[dict[str, Any]] = (),
    backend: str = "neo4j",
    limit: int = 10,
) -> dict[str, Any]:
    """Build the validation page's final trace without touching agent events."""

    stages = list(collected_stages)
    execution = result.execution_trace or RetrievalExecutionTrace(
        original_query=query,
        processed_query=query,
        search_switch=search_switch,
        backend=backend,
        limit=limit,
    )
    ranked_memories = [memory for memory in result.memories if memory.id != end_user_id]
    limited_memories = ranked_memories[: max(execution.limit, 0)]
    candidates = [
        project_retrieval_candidate(memory, rank)
        for rank, memory in enumerate(limited_memories, start=1)
    ]
    search_status = _search_status(execution)
    result_stage = _find_stage(stages, "result_ready")
    duration = (
        int(result_stage.get("data", {}).get("duration_ms", 0)) if result_stage else 0
    )
    return {
        "request_id": request_id,
        "status": search_status,
        "duration_ms": duration,
        "items": candidates,
    }


def memory_dumps(result: ReadResult) -> list[dict[str, Any]]:
    """The non-streaming face's ``intermediate_outputs``: raw memory item dumps.

    This deliberately differs from :func:`build_intermediate_outputs` (the
    streaming end event): the V1 API-key contract returns the memory objects
    themselves, not the display-oriented problem-split/search-result blocks.
    """

    return [memory.model_dump() for memory in result.memories]


def serialize_public_memory(memory: MemoryItem, end_user_id: str) -> dict[str, Any]:
    """Dump one memory for the public API-key face.

    The terminal-user profile item loses the eight historical profile fields
    from ``data`` (host ``_serialize_public_memory``); ordinary memory nodes are
    returned unchanged.
    """

    item = memory.model_dump()
    if not is_user_profile(memory, end_user_id):
        return item
    data = item.get("data")
    if isinstance(data, dict):
        item["data"] = {
            key: value
            for key, value in data.items()
            if key not in LEGACY_METADATA_FIELDS
        }
    return item


def public_memory_dumps(result: ReadResult, end_user_id: str) -> list[dict[str, Any]]:
    """``intermediate_outputs`` of the public ``/read/sync`` face."""

    return [serialize_public_memory(memory, end_user_id) for memory in result.memories]


# ────────────────────────────────────────────────────────────────
# Legacy intermediate outputs (streaming end event / non-streaming payload)
# ────────────────────────────────────────────────────────────────


def build_intermediate_outputs(
    result: ReadResult,
    search_switch: SearchStrategy | str,
) -> list[dict[str, Any]]:
    """Build the legacy ``intermediate_outputs`` list."""

    strategy = (
        search_switch
        if isinstance(search_switch, SearchStrategy)
        else SearchStrategy(str(search_switch))
    )
    outputs: list[dict[str, Any]] = []
    sub_queries = {str(memory.query) for memory in result.memories}
    if strategy in {SearchStrategy.DEEP, SearchStrategy.NORMAL}:
        outputs.append(
            {
                "type": "problem_split",
                "title": "问题拆分",
                "data": [
                    {"id": f"Q{index}", "question": question}
                    for index, question in enumerate(
                        (question for question in sub_queries if question),
                        start=1,
                    )
                ],
            }
        )

    perceptual_data = [
        memory.data
        for memory in result.memories
        if _source_value(memory.source) == MemorySource.PERCEPTUAL.value
    ]
    if perceptual_data:
        outputs.append(
            {
                "type": "perceptual_retrieve",
                "title": "感知记忆检索",
                "data": perceptual_data,
                "total": len(perceptual_data),
            }
        )

    outputs.append(
        {
            "type": "search_result",
            "title": (
                f"合并检索结果 (共{len(sub_queries)}个查询,{len(result.memories)}条结果)"
            ),
            "result": _escape_html(result.content),
            "raw_result": [memory.model_dump() for memory in result.memories],
            "total": len(result.memories),
        }
    )
    return outputs


def _escape_html(value: str) -> str:
    import html

    return html.escape(value)


def build_fast_search_stage(
    strategy: SearchStrategy,
    end_user_id: str,
    result: ReadResult,
) -> dict[str, Any] | None:
    """Synthesize a stage for strategies that do not emit real retrieval stages."""

    stage = {
        SearchStrategy.QUICK: "hybrid_searched",
        SearchStrategy.EXPRESS: "keyword_searched",
    }.get(strategy)
    if stage is None:
        return None
    memories = [memory for memory in result.memories if memory.id != end_user_id]
    items = project_memory_items(memories, limit=3)
    execution = result.execution_trace
    from src.core.application.read.stages import build_memory_stage_payload

    return build_memory_stage_payload(
        stage=stage,
        data={
            "hit_count": execution.raw_hit_count if execution is not None else len(memories),
            "memory_count": len(memories),
            "shown_count": len(items),
            "items": items,
        },
    )


# ────────────────────────────────────────────────────────────────
# Community trace projection (validation page)
# ────────────────────────────────────────────────────────────────


def project_community_trace(trace: CommunityRetrievalTrace) -> dict[str, Any]:
    """Full stage/answer snapshot; the SSE trace event projects a subset."""

    communities = [
        {
            "id": item["id"],
            "node_type": "SceneCommunity",
            "topic_scope": item["topic_scope"],
            "rank": rank,
            "score": item["score"],
        }
        for rank, item in enumerate(trace.selected_communities, 1)
    ]
    candidates = [
        {
            "id": item["id"],
            "node_type": "SceneSummary",
            "topic_scope": item["topic_scope"],
            "community_id": group["community_id"],
            "rank": rank,
            "score": item["score"],
        }
        for group in trace.members
        for rank, item in enumerate(group["candidates"], 1)
    ]
    scenes = {
        item["id"]: {**item, "community_id": group["community_id"]}
        for group in trace.members
        for item in group["candidates"]
    }
    items = [
        {
            "id": node_id,
            "node_type": "SceneSummary",
            "community_id": scenes[node_id]["community_id"],
            "title": scenes[node_id]["topic_scope"],
            "content": scenes[node_id]["content"],
            "score": scenes[node_id]["score"],
            "rank": rank,
            "evidence_no": rank,
        }
        for rank, node_id in enumerate(trace.evidence_ids, 1)
    ]
    return {
        "kind": "community",
        "status": trace.status,
        "backend": str(trace.backend or "neo4j").lower(),
        "semantic_degraded": trace.semantic_degraded,
        "score_type": "fulltext" if trace.semantic_degraded else "cosine",
        "empty_reason": trace.empty_reason,
        "duration_ms": trace.stage_duration_ms.get("total", 0),
        "communities": communities,
        "scenes": candidates,
        "packing": {
            "merged_scene_count": len(trace.merged_ranking),
            "evidence_count": len(items),
            "final_tokens": trace.final_tokens,
            "context_token_budget": trace.parameters["context_token_budget"],
            "budget_tokenizer": trace.parameters["budget_tokenizer"],
            "actions": [dict(action) for action in trace.packing_actions],
        },
        "items": items,
        "counts": {
            "matched_community_count": trace.matched_community_count,
            "recalled_community_count": trace.recalled_community_count,
            "selected_community_count": len(communities),
            "recalled_scene_count": sum(
                group["recalled_scene_count"] for group in trace.members
            ),
            "selected_scene_count": sum(
                len(group["candidates"]) for group in trace.members
            ),
            "evidence_count": len(items),
        },
    }


class CommunityStageCollector:
    """Request-local community stage events for the streaming face."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []


_community_events: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "memory_read_community_events", default=None
)


@contextmanager
def community_stage_collector() -> Iterator[list[dict[str, Any]]]:
    """Collect community stage snapshots for one streaming request."""

    events: list[dict[str, Any]] = []
    token = _community_events.set(events)
    try:
        yield events
    finally:
        _community_events.reset(token)


def emit_community_stage(
    trace: CommunityRetrievalTrace, stage: str, status: str
) -> None:
    """Record one community stage snapshot on the active request-local collector."""

    events = _community_events.get()
    if events is None:
        return
    snapshot = project_community_trace(trace)
    if stage == "community_searched":
        data = {"items": snapshot["communities"]}
    elif stage == "community_scenes_searched":
        data = {"items": snapshot["scenes"]}
    else:
        data = {"items": snapshot["items"]}
    events.append(deepcopy({"stage": stage, "status": status, "data": data}))


__all__ = [
    "CommunityStageCollector",
    "build_fast_search_stage",
    "build_intermediate_outputs",
    "build_validation_trace",
    "community_stage_collector",
    "display_text",
    "emit_community_stage",
    "memory_type",
    "profile_has_content",
    "project_community_trace",
    "project_memory_item",
    "project_memory_items",
    "project_profile_data",
    "project_relation_items",
    "project_result_items",
    "project_retrieval_candidate",
    "public_memory_dumps",
    "sanitize_relevance",
    "serialize_public_memory",
    "memory_dumps",
]