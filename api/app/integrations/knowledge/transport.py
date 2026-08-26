"""Private HTTP transport for the independent knowledge service."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from .call_profile import CallProfile
from .contracts import KnowledgeCallContext
from .errors import KnowledgeTimeoutError, KnowledgeUnavailableError

_REQUEST_HEADER_ALLOWLIST = frozenset({"accept", "accept-language", "content-type", "range"})
_RESPONSE_HEADER_ALLOWLIST = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-disposition",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
        "x-total-files",
        "x-trace-id",
    }
)


class KnowledgeHttpTransport:
    """Own one process-level HTTP pool and all transport normalization."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: httpx.Timeout,
        limits: httpx.Limits,
        stream_timeout: httpx.Timeout,
        health_timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            limits=limits,
            transport=transport,
            trust_env=False,
        )
        self._default_timeout = timeout
        self._stream_timeout = stream_timeout
        self._health_timeout = health_timeout

    @property
    def base_url(self) -> httpx.URL:
        return self._client.base_url

    def internal_url(self, path: str, query: bytes = b"") -> httpx.URL:
        return self._client.base_url.copy_with(path=path, query=query)

    @staticmethod
    def internal_path(external_path: str) -> str:
        for prefix in ("/api", "/v1"):
            if external_path == prefix or external_path.startswith(prefix + "/"):
                suffix = external_path[len(prefix) :]
                return "/internal/v1" + suffix
        if external_path.startswith("/internal/v1/"):
            return external_path
        raise ValueError("Knowledge route path must start with /api, /v1, or /internal/v1")

    @staticmethod
    def request_headers(
        incoming: Mapping[str, str],
        context: KnowledgeCallContext,
        profile: CallProfile,
    ) -> dict[str, str]:
        headers = {
            key: value
            for key, value in incoming.items()
            if key.lower() in _REQUEST_HEADER_ALLOWLIST
        }
        if profile is CallProfile.MULTIPART_UPLOAD:
            headers.pop("content-type", None)
            headers.pop("Content-Type", None)
        principal = context.principal
        if principal is not None:
            headers["X-KB-Actor-ID"] = str(principal.actor_id)
            if principal.actor_name:
                headers["X-KB-Actor-Name"] = principal.actor_name
            headers["X-KB-Tenant-ID"] = str(principal.tenant_id)
            headers["X-KB-Workspace-ID"] = str(principal.workspace_id)
        headers["X-KB-Source"] = context.source.value
        headers["X-Trace-Id"] = context.trace_id
        return headers

    @staticmethod
    def response_headers(incoming: Mapping[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in incoming.items()
            if key.lower() in _RESPONSE_HEADER_ALLOWLIST
        }

    async def send(
        self,
        *,
        method: str,
        url: httpx.URL,
        headers: Mapping[str, str],
        profile: CallProfile,
        content: bytes | AsyncIterator[bytes] | None = None,
        data: Any = None,
        files: Any = None,
    ) -> httpx.Response:
        timeout = (
            self._stream_timeout
            if profile in {CallProfile.MULTIPART_UPLOAD, CallProfile.STREAM_DOWNLOAD}
            else self._default_timeout
        )
        request = self._client.build_request(
            method,
            url,
            headers=headers,
            content=content,
            data=data,
            files=files,
            timeout=timeout,
        )
        try:
            return await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise KnowledgeTimeoutError("Knowledge service request timed out") from exc
        except httpx.RequestError as exc:
            raise KnowledgeUnavailableError("Knowledge service is unavailable") from exc

    async def ready(self) -> bool:
        url = self.internal_url("/internal/v1/health/ready")
        try:
            response = await self._client.get(url, timeout=self._health_timeout)
        except httpx.HTTPError:
            return False
        try:
            return response.status_code == 200
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()
