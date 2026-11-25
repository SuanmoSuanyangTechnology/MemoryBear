"""Transport adaptation for the migrated workspace re-embed endpoints.

Three groups share this handler:

* the re-embed status reads (the two job-detail routes and the two per-user
  lists) — the host returns the plain ``ApiResponse`` envelope, so the payload
  shapes are described by ``src.schemas.workspace_api``;
* the job-creation route called by the workspace service — it registers the
  pending job and publishes the driver;
* the four manual-retry routes — the retry logic itself now runs here, in
  ``ReembedRetryService``; only the downstream re-embed workers are still
  published to the API worker.

Errors keep the host's shape: an unknown ``status`` filter is the API's
``BusinessException(INVALID_PARAMETER)`` (HTTP status from the shared error-core
table, same code and message), and a missing/foreign job is the host's
``HTTPException(404, "reembed job not found")`` (same envelope and detail).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from error_core.codes import http_status_for
from error_core.messages import user_message
from fastapi import Request
from fastapi.responses import JSONResponse

from src.common.errors import BizCode
from src.common.responses import (
    ApiResponse,
    error_response,
    failure,
    http_error_response,
    server_error_response,
    success,
)
from src.i18n import get_request_language, t
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.workspace_api import (
    WorkspaceReembedJobCreate,
)
from src.services.reembed_jobs import (
    ReembedJobCreateConflictError,
    ReembedJobCreateInvalidError,
    ReembedJobService,
    ReembedWorkspaceNotFoundError,
)
from src.services.reembed_retry import (
    ReembedRetryConflictError,
    ReembedRetryService,
)
from src.services.reembed_status import (
    InvalidReembedStatusError,
    ReembedJobNotFoundError,
    ReembedStatusService,
)

logger = logging.getLogger(__name__)

#: Host literal message for "list current" when no job is visible.
NO_CURRENT_JOB_MSG = "no re-embed job"
#: Host ``HTTPException`` detail for a missing (or foreign) job.
REEMBED_JOB_NOT_FOUND_DETAIL = "reembed job not found"


def _ok_message(request: Request) -> str:
    """The host's default success message (``success(data=...)`` without ``msg``)."""

    return user_message(BizCode.OK, get_request_language(request)) or "成功"


class WorkspaceHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime
        self.reembed = ReembedStatusService(runtime.postgres)
        self.retry = ReembedRetryService(runtime)
        self.jobs = ReembedJobService(runtime)

    # ---------------------------------------------------- re-embed end users

    async def current_end_users(
        self,
        principal: MemoryPrincipal,
        request: Request,
        *,
        status: str | None,
        page: int,
        pagesize: int,
    ) -> ApiResponse[Any] | JSONResponse:
        try:
            data = await self.reembed.current_end_users(
                principal.workspace_id,
                status=status,
                page=page,
                pagesize=pagesize,
            )
        except InvalidReembedStatusError as exc:
            return _invalid_status(request, exc)
        # The host's "no current job" branch overrides the default success text;
        # a job that exists always has an id, so a null id is that branch.
        msg = (
            NO_CURRENT_JOB_MSG
            if data["job_id"] is None
            else _ok_message(request)
        )
        return success(data=data, msg=msg)

    async def job_end_users(
        self,
        principal: MemoryPrincipal,
        request: Request,
        *,
        job_id: UUID,
        status: str | None,
        page: int,
        pagesize: int,
    ) -> ApiResponse[Any] | JSONResponse:
        try:
            data = await self.reembed.job_end_users(
                principal.workspace_id,
                job_id,
                status=status,
                page=page,
                pagesize=pagesize,
            )
        except InvalidReembedStatusError as exc:
            return _invalid_status(request, exc)
        except ReembedJobNotFoundError:
            return http_error_response(
                request,
                404,
                REEMBED_JOB_NOT_FOUND_DETAIL,
                code=int(BizCode.NOT_FOUND),
            )
        return success(data=data, msg=_ok_message(request))

    # --------------------------------------------------- re-embed job detail

    async def current_job_detail(
        self, principal: MemoryPrincipal, request: Request
    ) -> ApiResponse[Any] | JSONResponse:
        """``GET /workspace_reembed/current``; no job is a 200 with ``data: {}``."""

        data = await self.reembed.current_job_detail(principal.workspace_id)
        if data is None:
            return success(data={}, msg=NO_CURRENT_JOB_MSG)
        return success(data=data, msg=_ok_message(request))

    async def job_detail(
        self, principal: MemoryPrincipal, request: Request, *, job_id: UUID
    ) -> ApiResponse[Any] | JSONResponse:
        """``GET /workspace_reembed/{job_id}`` (terminal jobs stay addressable)."""

        try:
            data = await self.reembed.job_detail(principal.workspace_id, job_id)
        except ReembedJobNotFoundError:
            return http_error_response(
                request,
                404,
                REEMBED_JOB_NOT_FOUND_DETAIL,
                code=int(BizCode.NOT_FOUND),
            )
        return success(data=data, msg=_ok_message(request))

    # ------------------------------------------------- re-embed job creation

    async def create_reembed_job(
        self,
        principal: MemoryPrincipal,
        request: Request,
        payload: WorkspaceReembedJobCreate,
    ) -> ApiResponse[Any] | JSONResponse:
        """Register a job for a model change and publish the API driver.

        The caller is the workspace service: it has already switched
        ``workspaces.embedding``. The row is committed here first; a broker
        failure leaves ``task_id`` null and the reconciler re-dispatches.
        """

        try:
            data = await self.jobs.create_and_dispatch(
                workspace_id=principal.workspace_id,
                tenant_id=principal.tenant_id,
                created_by_user_id=principal.actor_id,
                old_embedding_config_id=payload.old_embedding_config_id,
                new_embedding_config_id=payload.new_embedding_config_id,
                old_model_name=payload.old_model_name,
                new_model_name=payload.new_model_name,
            )
        except ReembedWorkspaceNotFoundError:
            return http_error_response(
                request,
                404,
                "workspace not found",
                code=int(BizCode.NOT_FOUND),
            )
        except ReembedJobCreateInvalidError as exc:
            return _invalid_parameter(request, exc)
        except ReembedJobCreateConflictError as exc:
            return _state_conflict(request, exc)
        except Exception:
            # The host's 500 body never carries the exception text: the generic
            # handler replaces it with the translated internal-error message and
            # keeps the traceback in the log only.
            logger.exception("reembed job creation failed")
            return _internal_error(request)
        return success(data=data, msg=_ok_message(request))

    # --------------------------------------------------- re-embed retry routes

    async def reembed_retry(
        self,
        principal: MemoryPrincipal,
        request: Request,
        *,
        mode: str,
        job_id: UUID | None = None,
        end_user_id: str | None = None,
    ) -> ApiResponse[Any] | JSONResponse:
        """Run one of the four manual-retry variants in this process.

        The DB transaction, Redis claim clearing and downstream dispatch are now
        the profile's (the API will be retired); only the re-embed workers are
        still published to the API worker.
        """

        try:
            result = await self.retry.retry(
                mode=mode,
                workspace_id=principal.workspace_id,
                job_id=job_id,
                end_user_id=end_user_id,
            )
        except ReembedJobNotFoundError:
            return http_error_response(
                request,
                404,
                REEMBED_JOB_NOT_FOUND_DETAIL,
                code=int(BizCode.NOT_FOUND),
            )
        except ReembedRetryConflictError as exc:
            return _state_conflict(request, exc)
        except Exception:
            # Same as job creation: `str(exc)` stays in the log, never in the body.
            logger.exception("reembed retry failed")
            return _internal_error(request)
        return success(data=result.data, msg=result.message or _ok_message(request))




def _invalid_status(
    request: Request, exc: InvalidReembedStatusError
) -> JSONResponse:
    """The host's ``BusinessException(INVALID_PARAMETER)`` envelope for a bad filter."""

    language = get_request_language(request)
    code = int(BizCode.INVALID_PARAMETER)
    return error_response(
        request,
        http_status_for(code),
        failure(code, user_message(code, language) or str(exc), str(exc)),
    )


def _state_conflict(request: Request, exc: Exception) -> JSONResponse:
    """The host's ``BusinessException(STATE_CONFLICT)`` envelope (HTTP 409)."""

    language = get_request_language(request)
    code = int(BizCode.STATE_CONFLICT)
    return error_response(
        request,
        http_status_for(code),
        failure(code, user_message(code, language) or str(exc), str(exc)),
    )


def _invalid_parameter(request: Request, exc: Exception) -> JSONResponse:
    """The host's ``BusinessException(INVALID_PARAMETER)`` envelope (HTTP 400)."""

    language = get_request_language(request)
    code = int(BizCode.INVALID_PARAMETER)
    return error_response(
        request,
        http_status_for(code),
        failure(code, user_message(code, language) or str(exc), str(exc)),
    )


def _internal_error(request: Request) -> JSONResponse:
    """The host's unhandled-exception body: ``10001`` and the generic text.

    The API's catch-all replaces the message with the translated internal-error
    string (production) or a sensitive-filtered one (development); this profile
    boundary has no such filter, so it never echoes the exception at all — the
    traceback is on the log line instead.
    """

    language = get_request_language(request)
    return server_error_response(
        request, t("common.http.internal_error", language), language
    )
