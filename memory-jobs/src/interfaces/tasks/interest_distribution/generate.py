"""Celery task that produces one user's interest distribution.

The generation runs under a fixed total budget and degrades to an empty
cacheable result rather than failing. The online path leaves the cache to its
caller (memory-profile); the dashboard pre-warm asks the unit to write it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName

from src.application.interest.generate import GenerateInterestDistributionCommand
from src.common.normalization import normalize_language, parse_uuid
from src.composition import get_container
from src.domain.shared.ids import WorkspaceId
from src.interfaces.tasks.base import JobTask

logger = logging.getLogger(__name__)


def _use_case():
    return get_container().generate_interest_distribution()


def _cache():
    return get_container().interest_cache()


@shared_task(
    name=TaskName.INTEREST_DISTRIBUTION_GENERATE.value,
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
    write_cache: bool = False,
) -> dict[str, Any]:
    """Generate one user's interest distribution."""

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")

    result = _use_case().execute(
        GenerateInterestDistributionCommand(
            end_user_id=end_user_id,
            workspace_id=WorkspaceId(ws_uuid),
            language=language,
            limit=limit,
        )
    ).to_payload()
    if write_cache and result.get("cacheable"):
        _cache().write(
            end_user_id, normalize_language(language), result.get("items") or []
        )
    logger.info(
        "interest distribution generated user=%s items=%s cacheable=%s elapsed=%.3fs",
        end_user_id,
        len(result.get("items") or []),
        result.get("cacheable"),
        time.monotonic() - started_at,
    )
    return result
