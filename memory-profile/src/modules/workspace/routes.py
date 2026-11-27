"""Internal routes for workspace re-embed and temporary-memory retention endpoints.

Paths mirror the monolith exactly (``/workspaces/...``) because the API proxy
forwards the caller's path unchanged after mapping ``/api`` or ``/v1`` onto
``/internal/v1``. The API-key face does not expose these routes, so no route
carries ``@external_api``.

For re-embed, the host controller declares a plain ``ApiResponse`` envelope.
A typed envelope would change the published response schema.
Two literals must stay ahead of their
``{job_id}`` siblings: ``/workspace_reembed/current`` before
``/workspace_reembed/{job_id}`` and ``/workspace_reembed/current/end_users``
before ``/{job_id}/end_users``, so "current" is never parsed as a UUID job id.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.i18n import get_request_language
from src.middleware.auth import SOURCE_INTERNAL, MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.workspace.handler import WorkspaceHandler
from src.schemas.workspace import (
    WorkspaceRetentionPolicyResponse,
    WorkspaceRetentionPolicyResult,
)
from src.schemas.workspace_api import (
    WorkspaceApiResponse,
    WorkspaceReembedJobCreate,
    WorkspaceRetentionPolicyUpdate,
)

router = APIRouter(prefix="/workspaces", tags=["Workspace"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> WorkspaceHandler:
    return WorkspaceHandler(request.app.state.runtime)


# ── Re-embed per-user status lists ───────────────────────────────────────────


@router.get(
    "/workspace_reembed/current/end_users",
    response_model=WorkspaceApiResponse,
)
async def list_current_workspace_reembed_end_users(
    request: Request,
    context: Context,
    status: Annotated[
        str | None, Query(description="queued/running/succeeded/failed")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).current_end_users(
        context, request, status=status, page=page, pagesize=pagesize
    )


@router.get(
    "/workspace_reembed/{job_id}/end_users",
    response_model=WorkspaceApiResponse,
)
async def list_workspace_reembed_end_users(
    request: Request,
    context: Context,
    job_id: UUID,
    status: Annotated[
        str | None, Query(description="queued/running/succeeded/failed")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).job_end_users(
        context,
        request,
        job_id=job_id,
        status=status,
        page=page,
        pagesize=pagesize,
    )


# ── Re-embed job detail ──────────────────────────────────────────────────────
# ``/workspace_reembed/current`` must stay before ``/workspace_reembed/{job_id}``:
# both are one segment, and "current" would otherwise be parsed as a job id (422).


@router.get(
    "/workspace_reembed/current",
    response_model=WorkspaceApiResponse,
)
async def get_current_workspace_reembed_job(
    request: Request,
    context: Context,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).current_job_detail(context, request)


@router.get(
    "/workspace_reembed/{job_id}",
    response_model=WorkspaceApiResponse,
)
async def get_workspace_reembed_job(
    request: Request,
    context: Context,
    job_id: UUID,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).job_detail(context, request, job_id=job_id)


# ── Re-embed job creation (workspace service → API worker) ───────────────────


@router.post(
    "/workspace_reembed/jobs",
    response_model=WorkspaceApiResponse,
)
async def create_workspace_reembed_job(
    request: Request,
    context: Context,
    body: WorkspaceReembedJobCreate,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="可选幂等键（8-128 字符）：重试时回放首次登记结果",
        ),
    ] = None,
) -> ApiResponse[Any] | JSONResponse:
    """Run the rest of a model switch; the caller only wrote the workspace row.

    Service-to-service entry point: the workspace service changes
    ``workspaces.embedding`` first and calls this route, which takes over
    everything the API's own switch flow did after that write:

    * resolve both configs to their underlying model name (``model_configs.name``)
      and skip the rebuild when the vector space did not change — a no-op is a
      success with ``created=False``, never an error;
    * one active job per workspace, and the target config must already be the
      workspace's current embedding model (otherwise 409);
    * commit the durable job row here and publish ``app.tasks.run_reembed_job``
      to the API worker;
    * invalidate ``memory_config:{config_id}`` for every config under the
      workspace so the new models take effect immediately.

    ``data.job_id`` is the job in flight after this request (newly created, or
    the one already running when no rebuild was needed) — the ``reembed_job_id``
    the caller back-fills into ``PUT /workspace_models``. The caller must roll
    its workspace columns back when this call fails: the column write is already
    committed by then.

    ``Idempotency-Key`` is optional but recommended: a retried registration with
    the same key replays the first response instead of registering a second job
    for the same model change. Without the header a short-lived key is derived
    from the body, which still covers transport-level retries.
    """

    return await _handler(request).create_reembed_job(
        context, request, body, idempotency_key=idempotency_key
    )


# ── Re-embed retry (profile-owned) ───────────────────────────────────────────


@router.post(
    "/workspace_reembed/current/end_users/retry_failed",
    response_model=WorkspaceApiResponse,
)
async def retry_failed_current_workspace_reembed_end_users(
    request: Request,
    context: Context,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).reembed_retry(
        context, request, mode="current_failed"
    )


@router.post(
    "/workspace_reembed/current/end_users/{end_user_id}/retry",
    response_model=WorkspaceApiResponse,
)
async def retry_current_workspace_reembed_end_user(
    request: Request,
    context: Context,
    end_user_id: str,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).reembed_retry(
        context, request, mode="current_one", end_user_id=end_user_id
    )


@router.post(
    "/workspace_reembed/{job_id}/end_users/retry_failed",
    response_model=WorkspaceApiResponse,
)
async def retry_failed_workspace_reembed_end_users(
    request: Request,
    context: Context,
    job_id: UUID,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).reembed_retry(
        context, request, mode="job_failed", job_id=job_id
    )


@router.post(
    "/workspace_reembed/{job_id}/end_users/{end_user_id}/retry",
    response_model=WorkspaceApiResponse,
)
async def retry_workspace_reembed_end_user(
    request: Request,
    context: Context,
    job_id: UUID,
    end_user_id: str,
) -> ApiResponse[Any] | JSONResponse:
    return await _handler(request).reembed_retry(
        context,
        request,
        mode="job_one",
        job_id=job_id,
        end_user_id=end_user_id,
    )


# ── Temporary-memory retention ──────────────────────────────────────────────


async def _require_user_context(request: Request) -> MemoryPrincipal:
    context = await require_memory_context(request)
    if context.source != SOURCE_INTERNAL or context.actor_kind != "actor":
        raise ProfileError(BizCode.FORBIDDEN, "User authentication required", status_code=403)
    return context


RetentionContext = Annotated[MemoryPrincipal, Depends(_require_user_context)]


@router.get(
    "/temporary-memory-retention", response_model=ApiResponse[WorkspaceRetentionPolicyResponse]
)
async def get_workspace_retention_policy(
    request: Request, context: RetentionContext
) -> ApiResponse[WorkspaceRetentionPolicyResponse]:
    return await _handler(request).get_workspace_retention_policy(
        context, get_request_language(request)
    )


@router.put(
    "/temporary-memory-retention", response_model=ApiResponse[WorkspaceRetentionPolicyResult]
)
async def update_workspace_retention_policy(
    request: Request, context: RetentionContext, policy: WorkspaceRetentionPolicyUpdate
) -> ApiResponse[WorkspaceRetentionPolicyResult]:
    return await _handler(request).update_workspace_retention_policy(
        context, policy, get_request_language(request)
    )
