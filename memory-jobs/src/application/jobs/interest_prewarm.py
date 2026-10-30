"""The dashboard interest pre-warm job.

It parses the caller's ids and renders ``PrewarmInterestDistributionsUseCase``;
filtering and publishing live in the use case. The dashboard never reads the
result, so the spec keeps it out of the result backend.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.application.dto import PrewarmReport
from src.application.interest.prewarm import PrewarmInterestDistributionsUseCase
from src.application.jobs.payload import integer, text, workspace_id_from
from src.application.ports.jobs import JobSpec, ResourceClass
from src.domain.shared.ids import EndUserId, parse_uuid
from src.domain.shared.language import Language

logger = logging.getLogger(__name__)

#: The API's pre-warm hardcoded zh at its display limit; keep both until the
#: dashboard gains a language preference.
DEFAULT_LANGUAGE = "zh"
DEFAULT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class PrewarmJobResult:
    """The use case's report plus what parsing this attempt added."""

    report: PrewarmReport
    invalid: int
    elapsed_seconds: float


class PrewarmInterestDistributionsJob:
    """Queue one generation unit per listed user that has no cached payload."""

    spec = JobSpec(
        resource_class=ResourceClass.BULK,
        acks_late=False,
        store_result=False,
    )

    def __init__(
        self,
        use_cases: Callable[[str, int], PrewarmInterestDistributionsUseCase],
    ) -> None:
        self._use_cases = use_cases

    def handle(
        self, payload: Mapping[str, Any], *, attempt_token: str
    ) -> PrewarmJobResult:
        started_at = time.monotonic()
        workspace_id = workspace_id_from(payload)
        language = Language.parse(
            text(payload, "language", DEFAULT_LANGUAGE)
        ).value
        limit = integer(payload, "limit", DEFAULT_LIMIT)

        raw_ids = payload.get("end_user_ids")
        if not isinstance(raw_ids, list):
            raise ValueError("end_user_ids must be a list of ids")
        candidates: list[EndUserId] = []
        invalid = 0
        for raw in raw_ids:
            parsed = parse_uuid(raw) if isinstance(raw, str) else None
            if parsed is None:
                invalid += 1
                logger.warning(
                    "interest pre-warm skipped an invalid user id user=%r", raw
                )
                continue
            candidates.append(EndUserId(parsed))

        report = self._use_cases(language, limit).execute(workspace_id, candidates)
        elapsed_seconds = time.monotonic() - started_at
        logger.info(
            "interest pre-warm dispatched=%s skipped_cached=%s skipped_missing=%s "
            "invalid=%s failed=%s elapsed=%.3fs",
            report.dispatched,
            report.skipped_cached,
            report.skipped_missing,
            invalid,
            report.failed,
            elapsed_seconds,
        )
        return PrewarmJobResult(
            report=report,
            invalid=invalid,
            elapsed_seconds=elapsed_seconds,
        )
