"""Manual re-embed retry use case (ported from the API controller/service).

The API's four ``workspace_reembed/.../retry`` endpoints used to run in-process:
resolve the job, run ``retry_job_users`` under a workspace lock, then publish the
downstream re-embed tasks. The profile now owns that flow; only the downstream
workers still live in the API until it is retired, and they are published by
their original task names (``run_reembed_job`` / ``do_reembed_end_user``).

Ordering is the API's and is load-bearing: state is committed first, then the
Redis claims are dropped and the tasks published. A dispatch failure therefore
leaves ``pending`` rows behind, which the periodic reconciler re-dispatches;
rolling back state to "fix" a publish error would lose the retry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.common.timing import utcnow_naive
from src.infrastructure.api_tasks import (
    dispatch_reembed_end_user,
    dispatch_reembed_job,
)
from src.repositories.reembed_status_mutation import (
    MODEL_CONFLICT,
    NOT_FOUND,
    NOTHING_RETRIED,
    RETRIED,
    USER_NOT_FAILED,
    ReembedRetryMutationRepository,
    RetryWriteOutcome,
)
from src.services.reembed_status import (
    REEMBED_MAX_ATTEMPTS,
    REEMBED_STALE_RUNNING_SECONDS,
    ReembedJobNotFoundError,
    ReembedStatusService,
)

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

#: The host's literal message for the "no current job" retry branch.
EMPTY_RETRY_MESSAGE = "no re-embed job"

#: The host's ``BusinessException(STATE_CONFLICT)`` messages, verbatim.
MODEL_CONFLICT_MESSAGE = "该重算任务的目标模型已不是当前工作空间的嵌入模型，无法重试"
USER_NOT_FAILED_MESSAGE = "该用户当前不是终态失败状态（可能仍在进行或已经完成），无需重试"

#: Redis keys owned by the API's ``job_state`` module. Same prefix and same
#: logical DB (``REDIS_DB``, default 1) on both services; a retried subtask must
#: not be rejected as ``already_inflight`` by the previous attempt's claim.
_CLAIM_KEY_PREFIX = "memory_reembed"


class ReembedRetryConflictError(Exception):
    """The host's ``BusinessException(STATE_CONFLICT)`` (HTTP 409)."""


@dataclass(slots=True)
class RetryResult:
    data: dict[str, Any]
    #: ``None`` means "use the caller's default success message" (host parity).
    message: str | None = None


def empty_job_retry_result() -> dict[str, Any]:
    """Payload for "no current job": same shape as a real retry, all zeroed."""

    return {
        "retried": 0,
        "reopened": False,
        "job_status": "",
        "end_user_ids": [],
    }


class ReembedRetryService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime
        self.status = ReembedStatusService(runtime.postgres)
        self.repository = ReembedRetryMutationRepository(runtime.postgres)

    async def retry(
        self,
        *,
        mode: str,
        workspace_id: UUID,
        job_id: UUID | None,
        end_user_id: str | None,
    ) -> RetryResult:
        """Run one of the four retry variants."""

        if mode.startswith("current"):
            job = await self.status.current_job(workspace_id)
            if job is None:
                # Not an error: the caller pressed a button when nothing needed
                # retrying. The host answers 200 with a zeroed payload.
                return RetryResult(empty_job_retry_result(), EMPTY_RETRY_MESSAGE)
            job_id = job["id"]
        elif job_id is None:
            raise ReembedJobNotFoundError("reembed job not found")

        outcome = await self.repository.retry(
            workspace_id=workspace_id,
            job_id=job_id,
            end_user_ids=[end_user_id] if end_user_id else None,
            max_attempts=REEMBED_MAX_ATTEMPTS,
            stale_before=utcnow_naive()
            - timedelta(seconds=REEMBED_STALE_RUNNING_SECONDS),
        )

        if outcome.kind == NOT_FOUND:
            raise ReembedJobNotFoundError(f"reembed job not found: {job_id}")
        if outcome.kind == MODEL_CONFLICT:
            raise ReembedRetryConflictError(MODEL_CONFLICT_MESSAGE)
        if outcome.kind == USER_NOT_FAILED:
            raise ReembedRetryConflictError(USER_NOT_FAILED_MESSAGE)

        await self._dispatch_after_commit(job_id, outcome)
        return RetryResult(outcome.payload)

    async def _dispatch_after_commit(
        self, job_id: UUID, outcome: RetryWriteOutcome
    ) -> None:
        publisher = self.runtime.api_tasks
        if outcome.kind == NOTHING_RETRIED:
            if outcome.dispatch_job:
                await self._clear_claim(_job_claim_key(job_id))
                await dispatch_reembed_job(publisher, job_id=job_id)
                logger.info(
                    "[MemoryReembed] job-level retry: job=%s end_user_rows=0 "
                    "(pre-flight failure)",
                    job_id,
                )
            return

        if outcome.kind != RETRIED:
            logger.error("unexpected retry outcome kind: %s", outcome.kind)
            return

        for end_user_id in outcome.payload["end_user_ids"]:
            # The previous attempt's claim may still be alive (TTL 5min); the
            # retried subtask would otherwise skip itself as already_inflight.
            await self._clear_claim(_end_user_claim_key(job_id, end_user_id))
            await dispatch_reembed_end_user(
                publisher, job_id=job_id, end_user_id=end_user_id
            )
        logger.info(
            "[MemoryReembed] manual retry: job=%s requeued=%s reopened=%s",
            job_id,
            outcome.payload["retried"],
            outcome.payload["reopened"],
        )

    async def _clear_claim(self, key: str) -> None:
        """Drop one coordination key; Redis failures must not fail the request.

        Mirrors the API's ``job_state._safe``: without Redis the retry still
        converges (duplicate work is wasted cost, not a correctness problem).
        """

        try:
            client = await self.runtime.redis.client()
            await client.delete(key)
        except Exception:  # noqa: BLE001 - degraded coordination only
            logger.warning("reembed claim clear failed: key=%s", key, exc_info=True)


def _job_claim_key(job_id: UUID) -> str:
    return f"{_CLAIM_KEY_PREFIX}:{job_id}:claim"


def _end_user_claim_key(job_id: UUID, end_user_id: str) -> str:
    return f"{_CLAIM_KEY_PREFIX}:{job_id}:user:{end_user_id}"


__all__ = [
    "EMPTY_RETRY_MESSAGE",
    "MODEL_CONFLICT_MESSAGE",
    "USER_NOT_FAILED_MESSAGE",
    "ReembedRetryConflictError",
    "ReembedRetryService",
    "RetryResult",
    "empty_job_retry_result",
]