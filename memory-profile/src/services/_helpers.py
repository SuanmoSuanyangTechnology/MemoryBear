"""Shared query helpers for the analytics application services."""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID
from collections.abc import Awaitable, Callable
from datetime import datetime

from src.repositories.graph import MemoryGraphRepository
from src.repositories.search import MemorySearchRepository

logger = logging.getLogger(__name__)


async def safe_count(name: str, call: Callable[[], Awaitable[int]]) -> int:
    """Run a counter and degrade to zero instead of failing the whole response."""

    try:
        return await call()
    except Exception:
        logger.warning("profile count degraded to zero: %s", name, exc_info=True)
        return 0


async def short_term_count(
    memory_search: MemorySearchRepository,
    memory_graph: MemoryGraphRepository,
    end_user_ids: list[str],
    now: datetime,
) -> int:
    """Use ES counts first, falling back for absent users or failed searches."""

    if not end_user_ids:
        return 0
    try:
        counts, present_ids = await memory_search.short_term_counts(end_user_ids, now)
    except Exception:
        logger.warning("Short-term ES count failed; falling back to Neo4j", exc_info=True)
        return await memory_graph.short_term_count(end_user_ids, now)
    missing_ids = [user_id for user_id in end_user_ids if user_id not in present_ids]
    total = sum(counts[user_id] for user_id in present_ids)
    if missing_ids:
        total += await memory_graph.short_term_count(missing_ids, now)
    return total


LEGACY_FIELDS = (
    "core_facts", "traits", "relations", "goals", "interests",
    "beliefs_or_stances", "anchors", "events",
)
PROFILE_FIELDS = (
    "name", "age", "from", "residence", "education", "occupation", "family",
    "religion_or_politics", "health", "person", "group", "object", "pet",
)


def _entry(value: Any) -> list[str] | None:
    if not isinstance(value, str):
        return None
    parts = [part.strip() for part in value.split("|", 2)]
    if len(parts) == 2:
        parts.insert(1, "")
    return parts if len(parts) == 3 and parts[2] else None


def count_profile_cards(metadata: Any, scene_names: dict[str, str]) -> int:
    metadata = metadata if isinstance(metadata, dict) else {}
    keys = {
        ("default", key) for key in LEGACY_FIELDS
        if metadata.get(key)
    }
    for field in ("identity_background", "relationships"):
        values = metadata.get(field)
        for value in values if isinstance(values, list) else []:
            entry = _entry(value)
            if entry and entry[0] in PROFILE_FIELDS:
                keys.add(("default", entry[0]))
    domains = metadata.get("domain_specific_info")
    for scene, values in domains.items() if isinstance(domains, dict) else []:
        if not isinstance(scene, str) or not scene.strip() or not isinstance(values, list):
            continue
        try:
            UUID(scene)
        except ValueError:
            name = scene
        else:
            name = scene_names.get(str(UUID(scene)))
            if not name:
                continue
        for value in values:
            entry = _entry(value)
            if entry and entry[0] and not entry[1]:
                keys.add(("domain", name, entry[0]))
    return len(keys)


def _valid_date(value: Any) -> bool:
    if value is None:
        return False
    try:
        raw = value.iso_format() if hasattr(value, "iso_format") else str(value)
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return True
    except (ValueError, TypeError):
        return False


def count_timeline_records(row: dict[str, Any]) -> int:
    if row.get("user_entity_count", 0) > 1:
        raise ValueError("User entity is not unique")
    events = 0
    for item in (row.get("event_timeline") or "").split("；"):
        match = re.match(r"^\[([^|]*)\|([^\]]*)\]\s*(.*)", item.strip())
        if not match:
            continue
        start, end, body = match.groups()
        start, end = start.strip(), end.strip()
        date = (start if start != "NULL" else "") or (end if end != "NULL" else "")
        parts = body.split("|")
        if len(parts) >= 4 and parts[0].strip() and _valid_date(date):
            events += 1
    summaries = set()
    for item in row.get("summaries") or []:
        text = item.get("text")
        if (
            text is not None and str(text).strip()
            and "MemorySummaryChunk" not in str(text)
            and _valid_date(item.get("created_at"))
        ):
            summaries.add(text)
    return events + len(summaries)
