"""Celery adapter for the unit-publisher port.

Celery is the delivery mechanism, so this adapter lives in the task package
rather than infrastructure. The task modules are imported lazily to keep the
package's registration imports acyclic.
"""

from __future__ import annotations

from src.application.ports.publishing import Priority
from src.interfaces.tasks.stagger import stagger_delay


class CeleryUnitPublisher:
    def publish_profile_cache(
        self,
        *,
        end_user_id: str,
        workspace_id: str,
        language: str,
        refresh_insight: bool,
        refresh_summary: bool,
        priority: Priority,
        index: int,
    ) -> None:
        from src.interfaces.tasks.profile_cache.generate import generate

        generate.apply_async(
            kwargs={
                "end_user_id": end_user_id,
                "workspace_id": workspace_id,
                "language": language,
                "refresh_insight": refresh_insight,
                "refresh_summary": refresh_summary,
            },
            countdown=stagger_delay(index),
            priority=int(priority),
        )

    def publish_interest_distribution(
        self,
        *,
        end_user_id: str,
        workspace_id: str,
        language: str,
        limit: int,
        write_cache: bool,
        priority: Priority,
        index: int,
    ) -> None:
        from src.interfaces.tasks.interest_distribution.generate import generate

        generate.apply_async(
            kwargs={
                "end_user_id": end_user_id,
                "workspace_id": workspace_id,
                "language": language,
                "limit": limit,
                "write_cache": write_cache,
            },
            countdown=stagger_delay(index),
            priority=int(priority),
        )
