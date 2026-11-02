"""Fan a workspace out into per-user cache units.

The use case validates the workspace once, lists its active users and publishes
one unit per user. Each unit re-validates its own workspace; this check only
keeps an unsupported workspace from queueing N units that would all fail.
"""

from __future__ import annotations

import logging
import time

from src.application.dto import FanOutReport
from src.application.ports.publishing import Priority, UnitPublisher
from src.application.ports.uow import UnitOfWork
from src.application.profile_cache.workspace_guard import (
    ensure_workspace_can_generate,
)
from src.domain.shared.ids import WorkspaceId

logger = logging.getLogger(__name__)


class FanOutWorkspaceUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        publisher: UnitPublisher,
        language: str,
    ) -> None:
        self.uow = uow
        self.publisher = publisher
        self.language = language

    def execute(self, workspace_id: WorkspaceId) -> FanOutReport:
        started_at = time.monotonic()

        with self.uow.read() as repositories:
            ensure_workspace_can_generate(
                repositories.workspaces.get(workspace_id), workspace_id
            )
            user_ids = repositories.end_users.active_ids_in(workspace_id)

        successful = 0
        errors: list[dict[str, object]] = []
        for index, user_id in enumerate(user_ids):
            try:
                self.publisher.publish_profile_cache(
                    end_user_id=str(user_id),
                    workspace_id=str(workspace_id),
                    language=self.language,
                    refresh_insight=True,
                    refresh_summary=True,
                    # Bulk units: the caller waits only for the dispatch summary,
                    # so they sit below a single-user refresh but above the
                    # nightly sweep.
                    priority=Priority.NORMAL,
                    dispatch_index=index,
                )
            except Exception as exc:
                errors.append({"end_user_id": str(user_id), "error": str(exc)})
                logger.error(
                    "profile cache workspace dispatch failed user=%s workspace=%s",
                    user_id,
                    workspace_id,
                    exc_info=True,
                )
                continue
            successful += 1

        logger.info(
            "profile cache workspace dispatched workspace=%s total=%s queued=%s failed=%s "
            "elapsed=%.3fs",
            workspace_id,
            len(user_ids),
            successful,
            len(errors),
            time.monotonic() - started_at,
        )
        return FanOutReport(
            total_users=len(user_ids),
            successful=successful,
            failed=len(errors),
            errors=errors,
        )
