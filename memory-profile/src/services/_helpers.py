"""Shared query helpers for the analytics application services."""

from __future__ import annotations

import logging
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
