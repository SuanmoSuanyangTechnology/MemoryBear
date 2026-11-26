"""Register a re-embed job for a workspace model change and dispatch its driver.

The workspace service owns the model switch itself; once ``workspaces.embedding``
points at the new config it calls ``POST /workspace_reembed/jobs`` so the durable
job row is registered here and the driver is handed to the API worker (the
re-embed workers are still API-owned until the API is retired).

Registration is idempotent: the caller's ``Idempotency-Key`` (or, when it sends
none, a key derived from the request body) reserves the operation in Redis before
anything is written, so a retried call replays the first response instead of
registering a second job for the same model change.

Registration carries the same guards as the API's own switch flow: a no-op change
(same config, or two configs resolving to the same underlying model) registers
nothing, one active job per workspace, and the job's target must already be the
workspace's current embedding model. Dispatch is best-effort, exactly like the
API's: the row is committed first, and a publish failure leaves it ``pending``
for the periodic reconciler to pick up.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from src.infrastructure.api_tasks import dispatch_reembed_job
from src.infrastructure.idempotency import (
    DEFAULT_TTL_SECONDS,
    DERIVED_TTL_SECONDS,
    MAX_KEY_LENGTH,
    MIN_KEY_LENGTH,
    IdempotencyReservation,
    IdempotencyStore,
    derived_key,
    request_fingerprint,
)
from src.repositories.reembed_status_mutation import (
    ACTIVE_JOB_EXISTS,
    CREATED,
    MODEL_NOT_CURRENT,
    WORKSPACE_NOT_FOUND,
    ReembedRetryMutationRepository,
)

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


class ReembedWorkspaceNotFoundError(Exception):
    """The workspace is missing or belongs to another tenant (host 404)."""


class ReembedJobCreateConflictError(Exception):
    """The current state does not allow registering a new job (host 409)."""


class ReembedJobCreateInvalidError(Exception):
    """The request is a no-op or self-contradictory (host 400)."""


class ReembedJobService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime
        self.repository = ReembedRetryMutationRepository(runtime.postgres)
        # ``getattr`` keeps the unit-test fakes (partial runtimes) working; a
        # missing manager just means "no reservation" (see IdempotencyStore).
        self.idempotency = IdempotencyStore(
            getattr(runtime, "redis", None), operation="reembed-job"
        )

    async def create_and_dispatch(
        self,
        *,
        workspace_id: UUID,
        tenant_id: UUID,
        created_by_user_id: UUID | None,
        old_embedding_config_id: str | None,
        new_embedding_config_id: str,
        old_model_name: str | None,
        new_model_name: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, str | None]:
        """Create the pending job and publish the driver; return both ids.

        ``task_id`` is ``None`` when the broker rejected the publish. That is not
        an error: the job is already committed and the reconciler re-dispatches
        it, so the caller may keep polling by ``job_id`` either way.

        A retried registration replays the first response: the key (the caller's
        header, or one derived from this request) is reserved before the write
        and completed with the result. A reused key with a different body, and a
        key whose first attempt is still running, are conflicts.
        """

        new_config = str(new_embedding_config_id)
        if (
            old_embedding_config_id is not None
            and str(old_embedding_config_id) == new_config
        ):
            raise ReembedJobCreateInvalidError(
                "old 与 new embedding 配置相同，无需登记重算任务"
            )

        # The switch flow registers a rebuild only when the *underlying model*
        # changes: swapping between two configs that resolve to the same model
        # keeps the vector space, so the host logs it and creates no job. Same
        # skip here, otherwise a caller that switches configs would start a
        # pointless full rebuild.
        if (
            old_model_name is not None
            and new_model_name is not None
            and old_model_name == new_model_name
        ):
            raise ReembedJobCreateInvalidError(
                "embedding 底层模型未变更（仅更换配置），无需登记重算任务"
            )

        fingerprint = request_fingerprint(
            {
                "workspace_id": str(workspace_id),
                "old_embedding_config_id": old_embedding_config_id,
                "new_embedding_config_id": new_config,
                "old_model_name": old_model_name,
                "new_model_name": new_model_name,
            }
        )
        key, ttl_seconds = self._resolve_key(idempotency_key, fingerprint)
        scope = f"{tenant_id}:{workspace_id}"
        reservation = await self._begin(scope, key, fingerprint, ttl_seconds)
        if reservation.state == "replay":
            if reservation.response is not None:
                logger.info(
                    "[MemoryReembed] registration replayed: workspace=%s key=%s",
                    workspace_id,
                    key,
                )
                return dict(reservation.response)
            # "done" with no readable response: treat it as still running rather
            # than registering the job a second time.
            reservation = IdempotencyReservation("in_progress")
        if reservation.state == "in_progress":
            raise ReembedJobCreateConflictError(
                "相同幂等键的重算任务登记仍在处理中，请稍后重试"
            )
        if reservation.state == "mismatch":
            raise ReembedJobCreateConflictError(
                "该幂等键已用于另一次不同的重算任务登记"
            )

        try:
            result = await self._register(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                created_by_user_id=created_by_user_id,
                old_embedding_config_id=old_embedding_config_id,
                new_config=new_config,
                old_model_name=old_model_name,
                new_model_name=new_model_name,
            )
        except Exception:
            await self._abort(scope, key, fingerprint)
            raise

        await self._complete(scope, key, fingerprint, result, ttl_seconds)
        return result

    async def _register(
        self,
        *,
        workspace_id: UUID,
        tenant_id: UUID,
        created_by_user_id: UUID | None,
        old_embedding_config_id: str | None,
        new_config: str,
        old_model_name: str | None,
        new_model_name: str | None,
    ) -> dict[str, str | None]:
        outcome = await self.repository.create_job(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            old_embedding_config_id=old_embedding_config_id,
            new_embedding_config_id=new_config,
            old_model_name=old_model_name,
            new_model_name=new_model_name,
            created_by_user_id=created_by_user_id,
        )

        if outcome.kind == WORKSPACE_NOT_FOUND:
            raise ReembedWorkspaceNotFoundError(f"workspace not found: {workspace_id}")
        if outcome.kind == ACTIVE_JOB_EXISTS:
            raise ReembedJobCreateConflictError(
                "该工作空间已有进行中的重算任务，不能重复登记"
            )
        if outcome.kind == MODEL_NOT_CURRENT:
            raise ReembedJobCreateConflictError(
                "新的 embedding 配置尚未成为工作空间当前模型，请先完成模型变更"
            )
        if outcome.kind != CREATED or outcome.job_id is None:
            raise RuntimeError(f"unexpected re-embed job outcome: {outcome.kind}")

        task_id = await dispatch_reembed_job(
            self.runtime.api_tasks, job_id=outcome.job_id
        )
        logger.info(
            "[MemoryReembed] job registered: job=%s workspace=%s task=%s",
            outcome.job_id,
            workspace_id,
            task_id,
        )
        return {"job_id": outcome.job_id, "task_id": task_id}

    @staticmethod
    def _resolve_key(
        idempotency_key: str | None, fingerprint: str
    ) -> tuple[str, int]:
        """The caller's key wins; otherwise derive one with a short window.

        A derived key is stable per request body, so a transport retry replays,
        while a *later* legitimately identical switch (after an intervening one
        in the other direction) falls outside the 60s window and registers.
        """

        if idempotency_key is None or not idempotency_key.strip():
            return derived_key(fingerprint), DERIVED_TTL_SECONDS
        key = idempotency_key.strip()
        if not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH:
            raise ReembedJobCreateInvalidError(
                f"Idempotency-Key 长度必须为 {MIN_KEY_LENGTH}-{MAX_KEY_LENGTH} 字符"
            )
        return key, DEFAULT_TTL_SECONDS

    async def _begin(
        self, scope: str, key: str, fingerprint: str, ttl_seconds: int
    ) -> IdempotencyReservation:
        try:
            return await self.idempotency.begin(
                scope=scope,
                key=key,
                fingerprint=fingerprint,
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            # Best-effort: a Redis outage must not block a model switch. The
            # worst case is a duplicate job, which the workspace guards and the
            # reconciler already tolerate.
            logger.warning(
                "re-embed idempotency reservation failed; proceeding unguarded",
                exc_info=True,
            )
            return IdempotencyReservation("new")

    async def _complete(
        self,
        scope: str,
        key: str,
        fingerprint: str,
        response: dict[str, str | None],
        ttl_seconds: int,
    ) -> None:
        try:
            await self.idempotency.complete(
                scope=scope,
                key=key,
                fingerprint=fingerprint,
                response=dict(response),
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            logger.warning(
                "re-embed idempotency completion failed; a retry may replay nothing",
                exc_info=True,
            )

    async def _abort(self, scope: str, key: str, fingerprint: str) -> None:
        try:
            await self.idempotency.abort(
                scope=scope, key=key, fingerprint=fingerprint
            )
        except Exception:
            logger.warning("re-embed idempotency release failed", exc_info=True)


__all__ = [
    "ReembedJobCreateConflictError",
    "ReembedJobCreateInvalidError",
    "ReembedJobService",
    "ReembedWorkspaceNotFoundError",
]