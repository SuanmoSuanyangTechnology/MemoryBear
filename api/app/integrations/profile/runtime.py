"""Process-level runtime for the optional memory profile integration."""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import settings

from .client import MemoryProfileServiceClient
from .errors import MemoryProfileConfigurationError


class MemoryProfileIntegrationRuntime:
    def __init__(self) -> None:
        self._client: MemoryProfileServiceClient | None = None
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def client(self) -> MemoryProfileServiceClient:
        if not self._enabled or self._client is None:
            raise MemoryProfileConfigurationError(
                "Remote memory profile integration is not enabled"
            )
        return self._client

    async def start(self, *, enabled: bool, base_url: str) -> None:
        await self.close()
        if not enabled:
            return

        normalized = base_url.strip()
        try:
            parsed = urlparse(normalized)
            _ = parsed.port
        except ValueError as exc:
            raise MemoryProfileConfigurationError(
                "MEMORY_PROFILE_BASE_URL is invalid"
            ) from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise MemoryProfileConfigurationError(
                "MEMORY_PROFILE_BASE_URL must be an absolute HTTP(S) origin"
            )
        if parsed.query or parsed.fragment or parsed.params:
            raise MemoryProfileConfigurationError(
                "MEMORY_PROFILE_BASE_URL must not contain params, query, or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise MemoryProfileConfigurationError(
                "MEMORY_PROFILE_BASE_URL must not contain a path"
            )

        self._client = MemoryProfileServiceClient.from_settings(
            settings,
            base_url=normalized,
        )
        self._enabled = True

    async def close(self) -> None:
        client = self._client
        self._client = None
        self._enabled = False
        if client is not None:
            await client.aclose()

    async def ready(self) -> bool:
        if not self._enabled:
            return True
        if self._client is None:
            return False
        return await self._client.ready()


_runtime = MemoryProfileIntegrationRuntime()


async def initialize_memory_profile_integration() -> None:
    await _runtime.start(
        enabled=settings.ENABLE_MEMORY_PROFILE,
        base_url=settings.MEMORY_PROFILE_BASE_URL,
    )


async def close_memory_profile_integration() -> None:
    await _runtime.close()


def is_memory_profile_enabled() -> bool:
    return _runtime.enabled


def get_memory_profile_client() -> MemoryProfileServiceClient:
    return _runtime.client


async def is_remote_memory_profile_ready() -> bool:
    return await _runtime.ready()
