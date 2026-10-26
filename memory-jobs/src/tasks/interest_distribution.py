"""Celery task that produces one user's interest distribution.

The generation runs under a fixed total budget and degrades to an empty
cacheable result rather than failing; the caller decides what to cache.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task

from src.common.normalization import parse_uuid
from src.modules.interest_distribution.service import InterestDistributionService
from src.runtime import get_runtime
from src.tasks.base import JobTask

logger = logging.getLogger(__name__)


def _service() -> InterestDistributionService:
    runtime = get_runtime()
    return InterestDistributionService(
        runtime.postgres, runtime.neo4j, runtime.model_service
    )


@shared_task(
    name="memory_jobs.interest_distribution.generate",
    base=JobTask,
    bind=True,
    ignore_result=False,
    max_retries=0,
    time_limit=180,
    soft_time_limit=150,
)
def generate(
    self: JobTask,
    end_user_id: str,
    workspace_id: str,
    language: str = "zh",
    limit: int = 5,
) -> dict[str, Any]:
    """Generate one user's interest distribution.

    The worker does not touch the interest cache: the caller owns the key, the
    TTL and the "cache the empty result too" rule, so the payload reports
    ``cacheable`` and the caller decides.
    """

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")

    result = _service().generate(ws_uuid, end_user_id, language, limit)
    logger.info(
        "interest distribution generated user=%s items=%s cacheable=%s elapsed=%.3fs",
        end_user_id,
        len(result.get("items") or []),
        result.get("cacheable"),
        time.monotonic() - started_at,
    )
    return result
