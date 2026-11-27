"""Register a re-embed job for a workspace model change and dispatch its driver.

The workspace service now performs only the workspace-column write; everything
after it runs here, mirroring the API's own switch flow
(``workspace_service.update_workspace_models_configs``):

* resolve both configs to their underlying model (``model_configs.name``, the
  host's ``model_name``) — the caller only sends ids;
* a slot that did not change, or two configs resolving to the same model,
  registers nothing (the vector space is unchanged) and is a success, not an
  error;
* one active job per workspace, and the job's target must already be the
  workspace's current embedding model;
* the durable job row is committed here and the driver is handed to the API
  worker (the re-embed workers are still API-owned until the API is retired);
* after the commit, every ``memory_config:{config_id}`` under the workspace is
  invalidated so the switch takes effect immediately.

Registration is idempotent: the caller's ``Idempotency-Key`` (or, when it sends
none, a key derived from the request body) reserves the operation in Redis before
anything is written, so a retried call replays the first response instead of
registering a second job for the same model change. No-op calls write nothing,
so they take no reservation.

The response's ``job_id`` is "the job in flight after this request" — the newly
registered one, the one already running when no rebuild was needed, or ``null``.
The caller must roll the workspace columns back itself when this call fails: the
column write is already committed by the time we see it, and a workspace that
points at a new model without a rebuild would mix two vector spaces.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.common.redis_cache import invalidate_cache
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
from src.repositories.memory_config import MemoryConfigReadRepository
from src.repositories.model_config import ModelConfigRepository
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
        self.models = ModelConfigRepository(runtime.postgres)
        self.configs = MemoryConfigReadRepository(runtime.postgres)
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
        old_embedding_config_id: UUID | None,
        new_embedding_config_id: UUID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Run the switch flow after the workspace columns are already written.

        ``task_id`` is ``None`` when no task was published — a no-op switch, or a
        broker that rejected the publish. Neither is an error: the job row (when
        one was created) is already committed and the reconciler re-dispatches
        it, so the caller may keep polling by ``job_id`` either way.

        A retried registration replays the first response: the key (the caller's
        header, or one derived from this request) is reserved before the write
        and completed with the result. A reused key with a different body, and a
        key whose first attempt is still running, are conflicts.
        """

        new_config = str(new_embedding_config_id)
        old_config = (
            None if old_embedding_config_id is None else str(old_embedding_config_id)
        )

        old_model_name, new_model_name = await self._resolve_model_names(
            tenant_id=tenant_id,
            old_config_id=old_embedding_config_id,
            new_config_id=new_embedding_config_id,
        )

        # The host registers a rebuild only when the embedding slot changed AND
        # the two configs resolve to different underlying models. Equal names
        # (including "both unresolved", exactly as the host compares them) mean
        # the vector space did not change. Both no-op branches are successes and
        # still invalidate the cached memory configs below, like the host, which
        # runs that side effect regardless of whether a job was created.
        slot_unchanged = old_config is not None and old_config == new_config
        model_unchanged = old_model_name == new_model_name
        if slot_unchanged or model_unchanged:
            await self._invalidate_memory_config_caches(workspace_id)
            return {
                "job_id": await self.repository.active_job_id(workspace_id),
                "task_id": None,
                "created": False,
            }

        fingerprint = request_fingerprint(
            {
                "workspace_id": str(workspace_id),
                "old_embedding_config_id": old_config,
                "new_embedding_config_id": new_config,
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
                old_embedding_config_id=old_config,
                new_config=new_config,
                old_model_name=old_model_name,
                new_model_name=new_model_name,
            )
        except Exception:
            await self._abort(scope, key, fingerprint)
            raise

        await self._complete(scope, key, fingerprint, result, ttl_seconds)
        # The host invalidates after its commit, whether or not a job was
        # created; a broker-rejected dispatch still counts as created here.
        await self._invalidate_memory_config_caches(workspace_id)
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
    ) -> dict[str, Any]:
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
        return {"job_id": outcome.job_id, "task_id": task_id, "created": True}

    async def _resolve_model_names(
        self,
        *,
        tenant_id: UUID,
        old_config_id: UUID | None,
        new_config_id: UUID | None,
    ) -> tuple[str | None, str | None]:
        """``model_configs.name`` for both configs — the host's ``model_name``.

        The host resolves through ``get_runtime_model_view_async`` and turns a
        resolution failure into ``None``; the names are then compared for
        equality. Same visibility rule here (the tenant's own rows plus public
        ones). A lookup error is not a "model unchanged" verdict: the host lets
        database errors fail the switch too.
        """

        ids = [model_id for model_id in (old_config_id, new_config_id) if model_id]
        rows: dict[UUID, dict[str, Any]] = {}
        if ids:
            rows = await self.models.get_models(ids, tenant_id)

        def _name(model_id: UUID | None) -> str | None:
            row = rows.get(model_id) if model_id is not None else None
            return None if row is None else row.get("name")

        return _name(old_config_id), _name(new_config_id)

    async def _invalidate_memory_config_caches(self, workspace_id: UUID) -> None:
        """Drop ``memory_config:{config_id}`` for the whole workspace.

        The cached configuration inherits the workspace's model slots, so the
        switch must not leave it behind. Best-effort like the host: a lookup or
        Redis failure is logged and never fails the switch.
        """

        try:
            config_ids = await self.configs.config_ids_by_workspace(workspace_id)
        except Exception:
            logger.warning(
                "re-embed: memory config lookup failed, caches not invalidated "
                "workspace=%s",
                workspace_id,
                exc_info=True,
            )
            return
        for config_id in config_ids:
            try:
                await invalidate_cache(prefix=f"memory_config:{config_id}")
            except Exception:
                logger.warning(
                    "re-embed: memory config cache invalidation failed config=%s",
                    config_id,
                    exc_info=True,
                )

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
        response: dict[str, Any],
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