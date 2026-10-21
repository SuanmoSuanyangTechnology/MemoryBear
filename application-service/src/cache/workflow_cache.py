"""Cross-process workflow-config cache contract (shared with the monolith).

``cache:workflow-config:v1:{app_id}`` is the only management-plane key shared
across processes during the coexistence period: the monolith's workflow read
path and the Celery trigger cache the app's resolved workflow under it, and
both sides invalidate through the same key. The service must reuse the exact
key — the service's own ``app:`` namespace prefix applies to its private
entries only, never to this shared key (same Redis instance and database).
Invalidation failures only log a warning; they must not fail the request.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def workflow_config_key(app_id: Any) -> str:
    return f"cache:workflow-config:v1:{app_id}"


def delete_workflow_config(redis_client: Any, app_id: Any) -> None:
    """Sync invalidation; swallows Redis errors like the monolith helper."""

    try:
        redis_client.delete(workflow_config_key(app_id))
    except Exception:
        logger.warning(
            "Redis cache invalidation failed: key=%s",
            workflow_config_key(app_id),
            exc_info=True,
        )


async def delete_workflow_config_async(redis_client: Any, app_id: Any) -> None:
    try:
        await redis_client.delete(workflow_config_key(app_id))
    except Exception:
        logger.warning(
            "Redis async cache invalidation failed: key=%s",
            workflow_config_key(app_id),
            exc_info=True,
        )


__all__ = ["delete_workflow_config", "delete_workflow_config_async", "workflow_config_key"]
