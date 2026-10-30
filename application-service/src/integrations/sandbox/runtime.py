"""Process-level lifecycle of the sandbox service client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ...config import current_settings

from .client import SandboxServiceClient
from .errors import SandboxConfigurationError

ClientFactory = Callable[[], Any]


class SandboxIntegrationRuntime:
    def __init__(self, client_factory: ClientFactory | None = None):
        self._client_factory = client_factory
        self._client: SandboxServiceClient | None = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> SandboxServiceClient:
        if self._client is None:
            raise SandboxConfigurationError(
                "Sandbox service integration is not enabled (set ENABLE_SANDBOX_SERVICE=true)"
            )
        return self._client

    async def start(self, *, enabled: bool, base_url: str, api_token: str) -> None:
        await self.close()
        if not enabled:
            return
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SandboxConfigurationError("SANDBOX_SERVICE_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise SandboxConfigurationError(
                "SANDBOX_SERVICE_BASE_URL must not contain query or fragment"
            )
        if not (api_token or "").strip():
            raise SandboxConfigurationError("SANDBOX_SERVICE_API_TOKEN must be set")
        factory = self._client_factory or _default_client_factory
        self._client = factory()

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def ready(self) -> bool:
        if self._client is None:
            return True
        return await self._client.ready()


def _default_client_factory() -> SandboxServiceClient:
    return SandboxServiceClient.from_settings(current_settings())


_runtime = SandboxIntegrationRuntime()


async def initialize_sandbox_integration() -> None:
    settings = current_settings()
    await _runtime.start(
        enabled=settings.enable_sandbox_service,
        base_url=settings.sandbox_service_base_url,
        api_token=settings.sandbox_service_api_token.get_secret_value(),
    )


async def close_sandbox_integration() -> None:
    await _runtime.close()


def is_sandbox_integration_enabled() -> bool:
    return _runtime.enabled


def get_sandbox_service_client() -> SandboxServiceClient:
    """Raises SandboxConfigurationError when the integration is disabled."""
    return _runtime.client


async def is_sandbox_service_ready() -> bool:
    return await _runtime.ready()
