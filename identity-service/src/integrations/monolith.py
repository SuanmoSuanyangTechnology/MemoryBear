"""Outbound client for the monolith identity-callback endpoints (W2-A).

During the coexistence period the ``workspace_models`` write unit stays in
the monolith so the workspace-column update and the reembed-job insert commit
in one transaction (decision D1). Identity only proxies that write and reads
the active reembed job id. Both hops authenticate with a dedicated shared
secret (``X-Callback-Secret``): no user token crosses the internal call.

Failure semantics follow the endpoint: the write hop surfaces transport
failures as ``SERVICE_UNAVAILABLE`` (never a silent success); the job-id read
degrades to ``None`` with an error log, because the monolith transaction
re-checks the active job on an actual switch anyway.
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

WORKSPACE_REEMBED_ACTIVE_PATH = "/api/internal/identity/workspace-reembed/active"
WORKSPACE_MODELS_PATH = "/api/internal/identity/workspace-models"


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.MONOLITH_CONNECT_TIMEOUT_SECONDS,
        read=settings.MONOLITH_READ_TIMEOUT_SECONDS,
        write=settings.MONOLITH_CONNECT_TIMEOUT_SECONDS,
        pool=settings.MONOLITH_CONNECT_TIMEOUT_SECONDS,
    )


def _callback_headers(
    *,
    user_id: UUID,
    tenant_id: UUID,
    workspace_id: UUID,
    language: str | None,
) -> dict[str, str]:
    secret = settings.IDENTITY_CALLBACK_SECRET
    if not secret:
        raise BusinessException(
            "内部回调凭据未配置", code=BizCode.SERVICE_UNAVAILABLE
        )
    headers = {
        "X-Callback-Secret": secret,
        "X-User-Id": str(user_id),
        "X-Tenant-Id": str(tenant_id),
        "X-Workspace-Id": str(workspace_id),
        "X-Language-Type": language or settings.I18N_DEFAULT_LANGUAGE,
    }
    trace_id = get_trace_id()
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return headers


def get_active_workspace_reembed_job_id(
    *,
    workspace_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Active (pending/running) reembed job id; any failure degrades to None."""

    try:
        with httpx.Client(timeout=_timeout(), transport=transport) as client:
            response = client.get(
                f"{settings.MONOLITH_BASE_URL.rstrip('/')}"
                f"{WORKSPACE_REEMBED_ACTIVE_PATH}",
                params={"workspace_id": str(workspace_id)},
                headers=_callback_headers(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    language=language,
                ),
            )
        if response.status_code != 200:
            raise RuntimeError(f"monolith returned {response.status_code}")
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"monolith rejected: {body.get('msg')}")
        data = body.get("data") or {}
        job_id = data.get("job_id")
        return None if job_id is None else str(job_id)
    except (httpx.HTTPError, RuntimeError, ValueError, BusinessException) as exc:
        logger.error(
            "[monolith] active reembed job lookup failed workspace_id=%s error=%s",
            workspace_id,
            exc,
            exc_info=True,
        )
        return None


def update_workspace_models(
    *,
    user_id: UUID,
    tenant_id: UUID,
    workspace_id: UUID,
    payload: dict[str, Any],
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    """Proxy the atomic workspace-model write; return (status, envelope).

    Business failures (validation issues, ``STATE_CONFLICT``, permission
    denials) are returned verbatim so the caller forwards code/status/message
    unchanged. Only transport and contract failures raise, mapped to
    ``SERVICE_UNAVAILABLE``.
    """

    headers = _callback_headers(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        language=language,
    )
    try:
        with httpx.Client(timeout=_timeout(), transport=transport) as client:
            response = client.put(
                f"{settings.MONOLITH_BASE_URL.rstrip('/')}{WORKSPACE_MODELS_PATH}",
                json=payload,
                headers=headers,
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("monolith returned a malformed body")
        return response.status_code, body
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.error(
            "[monolith] workspace-models write failed workspace_id=%s error=%s",
            workspace_id,
            exc,
            exc_info=True,
        )
        raise BusinessException(
            "老单体服务暂不可用", code=BizCode.SERVICE_UNAVAILABLE
        ) from exc


__all__ = [
    "get_active_workspace_reembed_job_id",
    "update_workspace_models",
]
