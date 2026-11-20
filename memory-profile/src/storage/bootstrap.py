"""Startup index confirmation for the ported storage layer.

Mirrors what the host does at boot:

* Elasticsearch — ``ensure_indices`` walks the registered label definitions and
  creates, updates or reindexes each versioned index behind its alias, guarded by
  the host's Redis migration lease (so several replicas never migrate at once);
* Neo4j — ``create_all_indexes`` upserts the fulltext / vector / range /
  composite indexes and the uniqueness constraints.

A search backend being unreachable must not take the profile down: failures are
logged and reported, and the service keeps serving reads.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.storage.provider.elasticsearch.index import ensure_indices
from src.storage.provider.neo4j.index.create_indexes import create_all_indexes

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

__all__ = ["ensure_storage_indexes"]


async def ensure_storage_indexes(runtime: ProfileRuntime) -> dict[str, Any]:
    """Confirm both search backends' index state; never raises.

    Returns a small report (``elasticsearch.changed`` / ``neo4j.ok`` / errors) so
    a caller can log or test the outcome. Each backend is handled independently:
    one failing does not skip the other.
    """

    report: dict[str, Any] = {}
    try:
        client = await runtime.elasticsearch.client()
        redis_client = await runtime.redis.client()
        changed = await ensure_indices(client, redis_client)
        report["elasticsearch"] = {"changed": list(changed)}
        if changed:
            logger.info(
                "storage indexes: Elasticsearch created/updated %s", ", ".join(changed)
            )
    except Exception:
        logger.warning("storage indexes: Elasticsearch ensure failed", exc_info=True)
        report["elasticsearch"] = {"error": "ensure_failed"}

    try:
        await create_all_indexes(runtime.neo4j)
        report["neo4j"] = {"ok": True}
    except Exception:
        logger.warning("storage indexes: Neo4j ensure failed", exc_info=True)
        report["neo4j"] = {"error": "ensure_failed"}

    return report
