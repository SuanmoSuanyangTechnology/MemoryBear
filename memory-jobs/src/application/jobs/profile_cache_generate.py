"""The per-user profile-cache job.

The use case owns the orchestration; this handler owns the parts that belong to
one task attempt: parsing the message, passing the attempt token to the lock,
and turning a result into an outcome the worker reports correctly. "Every
requested part failed" is a task failure, not a success holding empty columns;
a partial success stays a successful task with per-part errors.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from src.application.jobs.payload import (
    flag,
    required_text,
    text,
    workspace_id_from,
)
from src.application.ports.jobs import JobSpec, ResourceClass
from src.application.profile_cache.generate import (
    GenerateProfileCacheCommand,
    GenerateProfileCacheUseCase,
    ProfileCacheResult,
)

logger = logging.getLogger(__name__)


class GenerateProfileCacheJob:
    """Generate and persist one user's insight and/or summary cache."""

    spec = JobSpec(resource_class=ResourceClass.LLM_BOUND)

    def __init__(self, use_case: GenerateProfileCacheUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, payload: Mapping[str, Any], *, attempt_token: str
    ) -> ProfileCacheResult:
        started_at = time.monotonic()
        end_user_id = required_text(payload, "end_user_id")
        workspace_id = workspace_id_from(payload)
        refresh_insight = flag(payload, "refresh_insight", True)
        refresh_summary = flag(payload, "refresh_summary", True)

        outcome = self._use_case.execute(
            GenerateProfileCacheCommand(
                end_user_id=end_user_id,
                workspace_id=workspace_id,
                attempt_token=attempt_token,
                language=text(payload, "language", "zh"),
                refresh_insight=refresh_insight,
                refresh_summary=refresh_summary,
            )
        )
        if outcome.skipped_reason is not None:
            return outcome

        requested = [
            name
            for name, enabled in (
                ("insight", refresh_insight),
                ("summary", refresh_summary),
            )
            if enabled
        ]
        outcomes = [
            outcome.insight_success if name == "insight" else outcome.summary_success
            for name in requested
        ]
        if requested and not any(outcomes):
            # A unit that refreshed nothing it was asked to refresh is a task
            # failure, so a retry/replay is visible instead of an empty success.
            errors = {
                str(item.get("type")): item.get("error")
                for item in outcome.errors or []
            }
            raise RuntimeError(
                "all requested cache refreshes failed: "
                + ", ".join(
                    f"{name}_error={errors.get(name)!r}" for name in requested
                )
            )
        if requested and not all(outcomes):
            logger.warning(
                "profile cache generation produced a partial result user=%s workspace=%s "
                "errors=%s elapsed=%.3fs",
                end_user_id,
                workspace_id,
                outcome.errors,
                time.monotonic() - started_at,
            )
        return outcome
