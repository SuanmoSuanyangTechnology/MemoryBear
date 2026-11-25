"""Internal routes for the migrated workspace re-embed endpoints.

Paths mirror the monolith exactly (``/workspaces/...``) because the API proxy
forwards the caller's path unchanged after mapping ``/api`` or ``/v1`` onto
``/internal/v1``. The API-key face does not expose these routes, so no route
carries ``@external_api``.

``response_model`` is the API's *untyped* envelope, because the host controller
declares plain ``ApiResponse`` for these routes; a typed envelope would change
the published response schema. Two literals must stay ahead of their
``{job_id}`` siblings: ``/workspace_reembed/current`` before
``/workspace_reembed/{job_id}`` and ``/workspace_reembed/current/end_users``
before ``/{job_id}/end_users``, so "current" is never parsed as a UUID job id.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.workspace.handler import WorkspaceHandler
from src.schemas.workspace_api import (
    WorkspaceApiResponse,
    WorkspaceReembedJobCreate,
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
    """Register a job for a model switch and publish the API re-embed driver.

    Service-to-service entry point: the workspace service changes
    ``workspaces.embedding`` first, then calls this route so the durable job row
    is written here and ``app.tasks.run_reembed_job`` is published to the API
    worker. One active job per workspace; the target config must already be the
    workspace's current embedding model (otherwise 409).

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
