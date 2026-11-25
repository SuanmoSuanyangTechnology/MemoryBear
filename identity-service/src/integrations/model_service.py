"""Outbound client for model-service workspace model-selection validation.

The identity service owns workspace writes but not the model tables; static
checks and live probes run inside model-service. Fail closed: on any
transport or contract failure the caller receives ``verify_failed`` issues
for every requested probe slot, so a workspace write can never silently bind
an unverified model.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from src.config import settings
from src.trace import get_trace_id

logger = logging.getLogger(__name__)

WORKSPACE_MODEL_SELECTION_PATH = "/internal/v1/workspace-model-selection/validate"


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


__all__ = ["validate_workspace_model_selection"]
