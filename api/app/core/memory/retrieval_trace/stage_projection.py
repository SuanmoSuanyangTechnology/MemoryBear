"""Project memory retrieval results into the public display contract.

The projection deliberately depends only on memory-domain models.  It does not
know about SSE, database persistence, or LangChain internals.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.models.service_models import (
    Memory,
    MemorySearchResult,
    RelationMemory,
)

_PROFILE_FIELDS = (
    "aliases_name",
    "description",
    "core_facts",
    "goals",
    "interests",
    "relations",
    "beliefs_or_stances",
    "anchors",
    "events",
    "traits",
)
_ARRAY_PROFILE_FIELDS = tuple(field for field in _PROFILE_FIELDS if field != "description")
_TIME_PREFIX = re.compile(r"^\[\d{4}-\d{2}-\d{2}T[^]]+\]\s*")


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


def project_profile_data(result: MemorySearchResult | None) -> dict[str, Any]:
    """Return the stable profile shape, including empty fields."""
    source = result.memories[0].data if result and result.memories else {}
    source = source if isinstance(source, dict) else {}
    profile: dict[str, Any] = {
        "aliases_name": _profile_list(source.get("aliases_name", source.get("aliases"))),
        "description": _string_value(source.get("description"), 200),
    }
    for field in _ARRAY_PROFILE_FIELDS:
        if field == "aliases_name":
            continue
        profile[field] = _profile_list(source.get(field))
    return profile


def profile_has_content(profile: dict[str, Any]) -> bool:
    return any(bool(value) for value in profile.values())


def memory_type(source: Neo4jNodeType | str) -> str:
    value = source.value if isinstance(source, Neo4jNodeType) else str(source or "")
    if value == "memory_l0":
        return "profile"
    return {
        "Statement": "fact",
        "Chunk": "fact",
        "Dialogue": "fact",
        "ExtractedEntity": "entity",
        "MemorySummary": "summary",
        "Perceptual": "file",
        "Rag": "file",
    }.get(value, "unknown")


def _score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(score, 4) if math.isfinite(score) else 0.0


def sanitize_relevance(value: Any) -> float:
    """Backward-compatible legacy helper; new payloads use ``score``."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return round(min(max(score, 0.0), 1.0), 4)


def _memory_content(memory: Memory, kind: str) -> str:
    data = memory.data if isinstance(memory.data, dict) else {}
    if kind == "profile":
        profile = project_profile_data(MemorySearchResult(memories=[memory]))
        values = profile["core_facts"] or profile["aliases_name"] or ([profile["description"]] if profile["description"] else [])
        return display_text("；".join(values), 300)
    if memory_type(memory.source) == "entity":
        candidates = (data.get("description"), data.get("description_summary"), data.get("name"), memory.content)
    elif memory.source == Neo4jNodeType.PERCEPTUAL:
        candidates = (memory.content, data.get("summary"))
    elif memory.source == Neo4jNodeType.RAG:
        candidates = (memory.content,)
    else:
        candidates = (data.get("content"), memory.content)
    for candidate in candidates:
        text = _string_value(candidate, 300)
        if text:
            return text
    return ""


def project_memory_item(memory: Memory, rank: int, *, kind: str | None = None) -> dict[str, Any]:
    resolved_kind = kind or memory_type(memory.source)
    item: dict[str, Any] = {
        "rank": rank,
        "memory_type": resolved_kind,
        "source": memory.source.value if isinstance(memory.source, Neo4jNodeType) else str(memory.source),
        "score": 1.0 if resolved_kind == "profile" else _score(memory.score),
        "content": _memory_content(memory, resolved_kind),
    }
    # Rag 与 Perceptual 都展示为文件记忆，但只有 Perceptual 有稳定的文件元数据协议。
    if resolved_kind == "file" and memory.source == Neo4jNodeType.PERCEPTUAL:
        data = memory.data if isinstance(memory.data, dict) else {}
        file_data = {
            "file_name": display_text(data.get("file_name"), 1000),
            "file_path": display_text(data.get("file_path"), 2000),
            "file_type": display_text(data.get("file_type"), 200),
            "perceptual_type": data.get("perceptual_type"),
        }
        if any(value not in (None, "") for value in file_data.values()):
            item["file"] = file_data
    return item


def project_memory_items(memories: Iterable[Memory], limit: int = 3) -> list[dict[str, Any]]:
    return [project_memory_item(memory, index) for index, memory in enumerate(list(memories)[:max(limit, 0)], start=1)]


def project_relation_items(relations: Iterable[RelationMemory], limit: int = 3) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for relation in list(relations)[:max(limit, 0)]:
        items.append({
            "source": display_text(relation.source, 10_000),
            "relation": display_text(relation.relation, 10_000),
            "target": display_text(relation.target, 10_000),
            "target_desc": display_text(relation.target_desc, 200),
        })
    return items


def project_result_items(
    profile_result: MemorySearchResult | None,
    search_result: MemorySearchResult | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build the final authoritative list, with a non-empty profile first."""
    items: list[dict[str, Any]] = []
    profile = project_profile_data(profile_result)
    if profile_has_content(profile) and limit > 0:
        profile_memory = profile_result.memories[0] if profile_result and profile_result.memories else None
        if profile_memory:
            items.append(project_memory_item(profile_memory, 1, kind="profile"))
    if search_result:
        for memory in search_result.memories:
            if len(items) >= limit:
                break
            items.append(project_memory_item(memory, len(items) + 1))
    for index, item in enumerate(items, start=1):
        item["rank"] = index
    return items
