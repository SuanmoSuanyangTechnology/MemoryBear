"""Outbound client for the memory-profile reembed registration faces (W2-B).

Identity owns the workspace row and writes the model columns itself; the
memory domain runs everything after that write: resolving both configs to
their underlying model, the rebuild decision, the job row, the dispatch and
the ``memory_config`` cache invalidation. The caller contract is strict — on
any non-2xx answer the caller must roll its workspace columns back, so a
failed registration can never leave a workspace pointing at a new embedding
model without the rebuild job. Transport and contract failures therefore
raise ``SERVICE_UNAVAILABLE`` and business failures return the service's own
envelope verbatim, which reuses the shared error codes.

The active-job read only feeds the display field, so every failure degrades to
``None`` like the monolith callback it replaces.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from error_core.codes import BizCode

from src.config import settings
from src.core.exceptions import BusinessException
from src.trace import get_trace_id

logger = logging.getLogger(__name__)

WORKSPACE_REEMBED_JOBS_PATH = "/internal/v1/workspaces/workspace_reembed/jobs"
WORKSPACE_REEMBED_CURRENT_PATH = "/internal/v1/workspaces/workspace_reembed/current"

_SOURCE_INTERNAL = "in_api"
#: Job states that still hold the workspace's model switch open.
_ACTIVE_JOB_STATUSES = frozenset({"pending", "running"})


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.MEMORY_PROFILE_CONNECT_TIMEOUT_SECONDS,
        read=settings.MEMORY_PROFILE_READ_TIMEOUT_SECONDS,
        write=settings.MEMORY_PROFILE_CONNECT_TIMEOUT_SECONDS,
        pool=settings.MEMORY_PROFILE_CONNECT_TIMEOUT_SECONDS,
    )


def _headers(
    *,
    user_id: UUID,
    tenant_id: UUID,
    workspace_id: UUID,
    language: str | None,
) -> dict[str, str]:
    headers = {
        "x-source": _SOURCE_INTERNAL,
        "x-user-id": str(user_id),
        "x-tenant-id": str(tenant_id),
        "x-workspace-id": str(workspace_id),
        "X-Language-Type": language or settings.I18N_DEFAULT_LANGUAGE,
    }
    trace_id = get_trace_id()
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return headers


def register_reembed_job(
    *,
    user_id: UUID,
    tenant_id: UUID,
    workspace_id: UUID,
    old_embedding_config_id: str | None,
    new_embedding_config_id: str,
    idempotency_key: str | None = None,
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Register the rebuild job after the caller wrote the workspace columns.

    Returns ``(status_code, envelope)``; business failures (409 guards, 404,
    400) are passed through unchanged so the caller forwards them and rolls its
    columns back. Only transport and contract failures raise, mapped to
    ``SERVICE_UNAVAILABLE`` — a silent success here would leave the workspace
    on a new vector space with no rebuild.
    """

    headers = _headers(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        language=language,
    )
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        with httpx.Client(timeout=_timeout(), transport=transport) as client:
            response = client.post(
                f"{settings.MEMORY_PROFILE_BASE_URL.rstrip('/')}"
                f"{WORKSPACE_REEMBED_JOBS_PATH}",
                json={
                    "old_embedding_config_id": old_embedding_config_id,
                    "new_embedding_config_id": new_embedding_config_id,
                },
                headers=headers,
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("memory-profile returned a malformed body")
        return response.status_code, body
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.error(
            "[memory-profile] reembed registration failed workspace_id=%s error=%s",
            workspace_id,
            exc,
            exc_info=True,
        )
        raise BusinessException(
            "记忆服务暂不可用", code=BizCode.SERVICE_UNAVAILABLE
        ) from exc


def get_active_reembed_job_id(
    *,
    user_id: UUID,
    tenant_id: UUID,
    workspace_id: UUID,
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Active (pending/running) reembed job id; any failure degrades to None.

    The ``current`` face also returns a terminal job for 24h after it ended, so
    the state is filtered here to reproduce the monolith's active-only id.
    """

    try:
        with httpx.Client(timeout=_timeout(), transport=transport) as client:
            response = client.get(
                f"{settings.MEMORY_PROFILE_BASE_URL.rstrip('/')}"
                f"{WORKSPACE_REEMBED_CURRENT_PATH}",
                headers=_headers(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    language=language,
                ),
            )
        if response.status_code != 200:
            raise RuntimeError(f"memory-profile returned {response.status_code}")
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"memory-profile rejected: {body.get('msg')}")
        data = body.get("data") or {}
        if not isinstance(data, dict) or data.get("status") not in _ACTIVE_JOB_STATUSES:
            return None
        job_id = data.get("id")
        return None if job_id is None else str(job_id)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.error(
            "[memory-profile] active reembed job lookup failed workspace_id=%s error=%s",
            workspace_id,
            exc,
            exc_info=True,
        )
        return None


__all__ = [
    "register_reembed_job",
    "get_active_reembed_job_id",
]
