"""Pre-warm interest distributions for the users a dashboard just returned.

The use case filters the caller's list down to active users without a cached
payload and publishes one unit each. The unit is told to write the cache, which
is the point of a pre-warm: the next online request hits it.
"""

from __future__ import annotations

import logging

from src.application.dto import PrewarmReport
from src.application.ports.cache import InterestCache
from src.application.ports.publishing import Priority, UnitPublisher
from src.application.ports.uow import UnitOfWork
from src.domain.shared.ids import EndUserId, WorkspaceId

logger = logging.getLogger(__name__)


class PrewarmInterestDistributionsUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        cache: InterestCache,
        publisher: UnitPublisher,
        language: str,
        limit: int,
    ) -> None:
        self.uow = uow
        self.cache = cache
        self.publisher = publisher
        self.language = language
        self.limit = limit

    def execute(
        self, workspace_id: WorkspaceId, candidates: list[EndUserId]
    ) -> PrewarmReport:
        with self.uow.read() as repositories:
            active = {
                str(user_id)
                for user_id in repositories.end_users.active_ids_in(workspace_id)
            }

        dispatched = 0
        skipped_cached = 0
        skipped_missing = 0
        errors: list[dict[str, object]] = []
        # ``dict.fromkeys`` keeps the caller's order while collapsing repeats.
        for end_user_id in dict.fromkeys(candidates):
            key = str(end_user_id)
            if key not in active:
                skipped_missing += 1
                continue
            if self.cache.exists(key, self.language):
                skipped_cached += 1
                continue
            try:
                self.publisher.publish_interest_distribution(
                    end_user_id=key,
                    workspace_id=str(workspace_id),
                    language=self.language,
                    limit=self.limit,
                    write_cache=True,
                    # Pre-warm serves the next request, not a caller waiting now.
                    priority=Priority.NORMAL,
                    index=dispatched,
                )
            except Exception as exc:
                errors.append({"end_user_id": key, "error": str(exc)})
                logger.error(
                    "interest pre-warm dispatch failed user=%s workspace=%s",
                    key,
                    workspace_id,
                    exc_info=True,
                )
                continue
            dispatched += 1

        logger.info(
            "interest pre-warm dispatched=%s skipped_cached=%s skipped_missing=%s "
            "failed=%s",
            dispatched,
            skipped_cached,
            skipped_missing,
            len(errors),
        )
        return PrewarmReport(
            dispatched=dispatched,
            skipped_cached=skipped_cached,
            skipped_missing=skipped_missing,
            failed=len(errors),
            errors=errors,
        )
