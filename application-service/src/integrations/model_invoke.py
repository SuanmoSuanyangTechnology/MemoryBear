"""Minimal sync client for the model-service internal invoke endpoint.

East-west direct dependency (never through the gateway): the host management
plane generated annotation embeddings through the monolith's model transport
chain; this service speaks the same wire directly — ``POST /internal/v1/invoke``
with the attribution headers and the ``{code, msg, data}`` envelope, sync
transport only (management plane). Credential decryption and channel routing
happen inside model-service; this client never carries model credentials.

The monolith treats a failed embedding call as a degraded write (warning +
``embedding=None``), so callers here wrap invocations and keep that posture;
this client itself fails loudly (``ModelInvokeError``).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx

from ..config import AppServiceSettings
from ..trace import get_trace_id

logger = logging.getLogger(__name__)

# Wire constants shared with the host internal-invoke protocol.
INVOKE_PATH = "/internal/v1/invoke"
# Attribution source value the host internal API face sends (`in_api`).
MODEL_SOURCE_INTERNAL_API = "in_api"
HEADER_TENANT_ID = "X-Model-Tenant-ID"
HEADER_SOURCE = "X-Model-Source"
HEADER_ACTOR_ID = "X-Model-Actor-ID"
HEADER_WORKSPACE_ID = "X-Model-Workspace-ID"
TRACE_ID_HEADER = "X-Trace-Id"

# Budgets aligned with the host invoke timeouts (connect 5s / inter-frame idle
# 60s / write 60s); the sync path reads the whole envelope, so `read` bounds the
# whole call.
_CONNECT_TIMEOUT_SECONDS = 5.0
_READ_TIMEOUT_SECONDS = 60.0
_WRITE_TIMEOUT_SECONDS = 60.0


class ModelInvokeError(RuntimeError):
    """The invoke channel is unconfigured, unreachable, or the envelope failed."""


class ModelInvokeClient:
    """Lazy sync HTTP client for the model-service internal invoke face."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._client: httpx.Client | None = None

    @classmethod
    def from_settings(cls, settings: AppServiceSettings) -> ModelInvokeClient:
        return cls(base_url=settings.model_service_base_url or "")

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    connect=_CONNECT_TIMEOUT_SECONDS,
                    read=_READ_TIMEOUT_SECONDS,
                    write=_WRITE_TIMEOUT_SECONDS,
                ),
                transport=self._transport,
                # Internal east-west calls must not pick up ambient proxy env.
                trust_env=False,
            )
        return self._client

    def embed_texts(
        self,
        config_id: UUID | str,
        texts: list[str],
        *,
        tenant_id: UUID | str,
        workspace_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
        trace_id: str | None = None,
    ) -> list[list[float]]:
        """Generate one embedding vector per input text via model-service.

        ``config_id`` references an embedding model config; the tenant id is a
        required attribution header (model-service rejects calls without it).
        """

        if not self.configured:
            raise ModelInvokeError("model service invoke channel is not configured")

        headers = {
            "Accept": "application/json",
            HEADER_TENANT_ID: str(tenant_id),
            HEADER_SOURCE: MODEL_SOURCE_INTERNAL_API,
        }
        if actor_id is not None:
            headers[HEADER_ACTOR_ID] = str(actor_id)
        if workspace_id is not None:
            headers[HEADER_WORKSPACE_ID] = str(workspace_id)
        resolved_trace = trace_id or get_trace_id() or None
        if resolved_trace:
            headers[TRACE_ID_HEADER] = resolved_trace

        body = {
            "config_id": str(config_id),
            "type": "embedding",
            "stream": False,
            "params": {"input": list(texts)},
        }

        try:
            response = self._get_client().post(INVOKE_PATH, json=body, headers=headers)
        except httpx.HTTPError as error:
            raise ModelInvokeError(f"model service invoke failed: {error}") from error

        data = self._unwrap_envelope(response)
        vectors = data.get("vectors") if isinstance(data, dict) else None
        if not isinstance(vectors, list):
            raise ModelInvokeError("model service response is missing 'vectors'")
        return [list(vector) for vector in vectors]

    @staticmethod
    def _unwrap_envelope(response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as error:
            raise ModelInvokeError(
                f"model service returned a malformed envelope (status={response.status_code})"
            ) from error
        if not isinstance(payload, dict) or payload.get("code") is None:
            raise ModelInvokeError("model service envelope is missing 'code'")
        code = payload["code"]
        if code:
            message = payload.get("msg") or payload.get("message") or str(code)
            raise ModelInvokeError(f"model service invoke rejected: {message}")
        return payload.get("data")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["INVOKE_PATH", "MODEL_SOURCE_INTERNAL_API", "ModelInvokeClient", "ModelInvokeError"]
