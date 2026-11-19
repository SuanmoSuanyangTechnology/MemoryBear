"""Read-only PostgreSQL access to the re-embedding state machine.

The read path asks one question before a vector search: have this end user's
stored vectors been rebuilt for the workspace's *current* embedding model yet?
Until they have, the stored vectors live in the old model's space while the query
vector comes from the new one, so semantic retrieval would return unrelated
(same dimension) or nothing at all (different dimension); the read must degrade
to keyword-only retrieval for that user.

This is the API's ``should_degrade_semantic`` ported verbatim: only the latest
job for the target model counts (a later, newer job supersedes an earlier
success), and inside it only a ``done`` row for this end user lets the read
through. The host caches the answer in Redis for 60s; this service has no such
cache, so the statement runs per check — it is two index lookups.

Read-only: this repository only selects, so it is listed with the other
read-only PostgreSQL repositories in the boundary script.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import MemoryReembedJobRow, MemoryReembedJobUserRow

#: Job state (api ``ReembedJobStatus``) that means "the rebuild finished".
JOB_STATUS_SUCCEEDED = "succeeded"
#: Per-user state (api ``ReembedUserStatus``) that means "this user was rebuilt".
USER_STATUS_DONE = "done"


class ReembedReadRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def semantic_rebuild_pending(
        self,
        *,
        workspace_id: UUID,
        end_user_id: str,
        embedding_model_id: UUID,
    ) -> bool:
        """Whether the latest rebuild for ``embedding_model_id`` left this user unfinished.

        ``True`` means "degrade": the latest job for that model did not succeed,
        and the job has no ``done`` row for this end user. A job that succeeded
        at the time of the switch cannot gate users added afterwards (they have
        no detail row), which is why the success of the latest job is checked
        first and the per-user row only afterwards.
        """

        latest_job = (
            select(MemoryReembedJobRow.id, MemoryReembedJobRow.status)
            .where(
                MemoryReembedJobRow.workspace_id == workspace_id,
                MemoryReembedJobRow.new_embedding_config_id == str(embedding_model_id),
            )
            .order_by(
                MemoryReembedJobRow.created_at.desc(),
                MemoryReembedJobRow.id.desc(),
            )
            .limit(1)
            .subquery()
        )
        done_row = select(MemoryReembedJobUserRow.id).where(
            MemoryReembedJobUserRow.job_id == latest_job.c.id,
            MemoryReembedJobUserRow.end_user_id == end_user_id,
            MemoryReembedJobUserRow.status == USER_STATUS_DONE,
        )
        statement = (
            select(latest_job.c.id)
            .where(
                latest_job.c.status != JOB_STATUS_SUCCEEDED,
                ~exists(done_row),
            )
            .limit(1)
        )
        async with self.postgres.session() as session:
            return (await session.scalar(statement)) is not None
