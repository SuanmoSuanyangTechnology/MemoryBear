"""The per-user interest-distribution job.

The generation runs under a fixed total budget and degrades to an empty
cacheable result rather than failing. The online path leaves the cache to its
caller (memory-profile); the dashboard pre-warm asks the unit to write it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from src.application.interest.generate import (
    GenerateInterestDistributionCommand,
    GenerateInterestDistributionUseCase,
    InterestDistributionResult,
)
from src.application.jobs.payload import (
    flag,
    integer,
    required_text,
    text,
    workspace_id_from,
)
from src.application.ports.jobs import JobSpec, ResourceClass

logger = logging.getLogger(__name__)


class GenerateInterestDistributionJob:
    """Generate one user's interest distribution."""

    spec = JobSpec(resource_class=ResourceClass.QUICK)

    def __init__(self, use_case: GenerateInterestDistributionUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, payload: Mapping[str, Any], *, attempt_token: str
    ) -> InterestDistributionResult:
        started_at = time.monotonic()
        end_user_id = required_text(payload, "end_user_id")

        result = self._use_case.execute(
            GenerateInterestDistributionCommand(
                end_user_id=end_user_id,
                workspace_id=workspace_id_from(payload),
                language=text(payload, "language", "zh"),
                limit=integer(payload, "limit", 5),
                write_cache=flag(payload, "write_cache", False),
            )
        )
        logger.info(
            "interest distribution generated user=%s items=%s cacheable=%s elapsed=%.3fs",
            end_user_id,
            len(result.items),
            result.cacheable,
            time.monotonic() - started_at,
        )
        return result
