"""Outbound client for the model-service internal endpoints.

The identity service owns the workspace surface but not the model tables.
Selection validation runs inside model-service and fails closed: on any
transport or contract failure the caller receives ``verify_failed`` issues
for every requested probe slot, so a workspace write can never silently bind
an unverified model. The options/items reads surface failures to the caller
as ``SERVICE_UNAVAILABLE`` instead: the routes have no safe local fallback.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from error_core.codes import BizCode

from src.config import settings
from src.core.exceptions import BusinessException
from src.trace import get_trace_id

logger = logging.getLogger(__name__)

WORKSPACE_MODEL_SELECTION_PATH = "/internal/v1/workspace-model-selection/validate"
WORKSPACE_MODEL_OPTIONS_PATH = "/internal/v1/workspace-model-options"
WORKSPACE_MODEL_OPTION_ITEMS_PATH = "/internal/v1/workspace-model-options/items"


def _headers(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    actor_name: str | None,
    language: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Model-Actor-ID": str(actor_id),
        "X-Model-Tenant-ID": str(tenant_id),
        "X-Model-Source": "identity-service",
        "X-Language-Type": language or settings.I18N_DEFAULT_LANGUAGE,
    }
    if actor_name:
        # Header values are ASCII-only; model-service percent-decodes this back.
        headers["X-Model-Actor-Name"] = quote(actor_name, safe="")
    trace_id = get_trace_id()
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return headers


def _degraded(probe_slots: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    return {
        "issues": [
            {
                "slot": slot,
                "reason": "verify_failed",
                "model_id": None,
                "model_name": None,
                "provider": None,
                "model_type": None,
                "detail": "model service unavailable",
            }
            for slot in probe_slots
        ]
    }


def validate_workspace_model_selection(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    actor_name: str | None,
    selection: dict[str, str | None],
    static_check: bool = False,
    probe_slots: Iterable[str] = (),
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Call the model-service selection gate; degrade fail-closed on errors."""

    slots = list(probe_slots)
    timeout = httpx.Timeout(
        connect=settings.MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS,
        read=settings.MODEL_SERVICE_READ_TIMEOUT_SECONDS,
        write=settings.MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS,
        pool=settings.MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.post(
                f"{settings.MODEL_SERVICE_BASE_URL.rstrip('/')}"
                f"{WORKSPACE_MODEL_SELECTION_PATH}",
                json={
                    "selection": selection,
                    "static_check": static_check,
                    "probe_slots": slots,
                },
                headers=_headers(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    language=language,
                ),
            )
        if response.status_code != 200:
            raise RuntimeError(f"model service returned {response.status_code}")
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"model service rejected: {body.get('msg')}")
        data = body.get("data") or {}
        issues = data.get("issues")
        if not isinstance(issues, list):
            raise RuntimeError("model service returned malformed data")
        return {"issues": issues}
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.error(
            "[workspace-model-selection] degraded tenant_id=%s static_check=%s error=%s",
            tenant_id,
            static_check,
            exc,
            exc_info=True,
        )
        return _degraded(slots)


def _request_data(
    *,
    method: str,
    path: str,
    tenant_id: UUID,
    actor_id: UUID,
    actor_name: str | None,
    json_body: dict[str, Any] | None = None,
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    """Call a model-service internal endpoint and return its ``data`` payload.

    Failures are translated to ``SERVICE_UNAVAILABLE`` at this boundary so the
    route layer never sees ``httpx`` exceptions.
    """

    timeout = httpx.Timeout(
        connect=settings.MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS,
        read=settings.MODEL_SERVICE_READ_TIMEOUT_SECONDS,
        write=settings.MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS,
        pool=settings.MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.request(
                method,
                f"{settings.MODEL_SERVICE_BASE_URL.rstrip('/')}{path}",
                json=json_body,
                headers=_headers(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    language=language,
                ),
            )
        if response.status_code != 200:
            raise RuntimeError(f"model service returned {response.status_code}")
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"model service rejected: {body.get('msg')}")
        return body.get("data")
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        logger.error(
            "[model-service] request failed tenant_id=%s path=%s error=%s",
            tenant_id,
            path,
            exc,
            exc_info=True,
        )
        raise BusinessException(
            "模型服务暂不可用", code=BizCode.SERVICE_UNAVAILABLE
        ) from exc


def get_workspace_model_options(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    actor_name: str | None,
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Six-slot candidate catalog grouped by model-service."""

    data = _request_data(
        method="GET",
        path=WORKSPACE_MODEL_OPTIONS_PATH,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        language=language,
        transport=transport,
    )
    if not isinstance(data, dict):
        logger.error("[model-service] malformed options payload tenant_id=%s", tenant_id)
        raise BusinessException(
            "模型服务暂不可用", code=BizCode.SERVICE_UNAVAILABLE
        )
    return data


def get_workspace_model_option_items(
    *,
    tenant_id: UUID,
    actor_id: UUID,
    actor_name: str | None,
    raw_ids: Iterable[str],
    language: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Load option items by id without deprecation/channel filtering."""

    data = _request_data(
        method="POST",
        path=WORKSPACE_MODEL_OPTION_ITEMS_PATH,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        json_body={"ids": list(raw_ids)},
        language=language,
        transport=transport,
    )
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        logger.error("[model-service] malformed option items tenant_id=%s", tenant_id)
        raise BusinessException(
            "模型服务暂不可用", code=BizCode.SERVICE_UNAVAILABLE
        )
    return items


__all__ = [
    "validate_workspace_model_selection",
    "get_workspace_model_options",
    "get_workspace_model_option_items",
]
