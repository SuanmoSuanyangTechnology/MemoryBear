"""Register a re-embed job for a workspace model change and dispatch its driver.

The workspace service owns the model switch itself; once ``workspaces.embedding``
points at the new config it calls ``POST /workspace_reembed/jobs`` so the durable
job row is registered here and the driver is handed to the API worker (the
re-embed workers are still API-owned until the API is retired).

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
    ) -> dict[str, str | None]:
        """Create the pending job and publish the driver; return both ids.

        ``task_id`` is ``None`` when the broker rejected the publish. That is not
        an error: the job is already committed and the reconciler re-dispatches
        it, so the caller may keep polling by ``job_id`` either way.
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


__all__ = [
    "ReembedJobCreateConflictError",
    "ReembedJobCreateInvalidError",
    "ReembedJobService",
    "ReembedWorkspaceNotFoundError",
]