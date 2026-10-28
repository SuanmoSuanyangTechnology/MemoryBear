"""API-key snapshot backfill against identity's internal verify endpoint.

The gateway never reads the database: on a Redis snapshot miss it asks identity
(credential authority) to rebuild the payload and caches it locally. A 200
response carries the snapshot fields plus `expires_at`, which only sizes the
Redis TTL and is never stored. Identity failures surface as
ApiKeyVerifyUnavailable and the termination pipeline stays fail-closed (401).
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime

import httpx
from auth_sdk.api_key import ApiKeyVerifier
from auth_sdk.schema import ApiKeyContext


class IdentityApiKeyBackfill:
    def __init__(self, verify_url: str, timeout_ms: int = 500,
                 client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout_ms / 1000)
        self._verifier = ApiKeyVerifier(verify_url, client=self._client,
                                        timeout_ms=timeout_ms)

    async def __call__(
        self, api_key: str
    ) -> tuple[ApiKeyContext, datetime | None] | None:
        claims = await self._verifier.verify(api_key)
        if claims is None:
            return None
        expires_raw = claims.pop("expires_at", None)
        expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
        # Read-side tolerance: unknown fields added by identity must not turn a
        # valid key into a fail-closed 401.
        known = {f.name for f in fields(ApiKeyContext)}
        return ApiKeyContext(**{k: v for k, v in claims.items() if k in known}), expires_at

    async def aclose(self) -> None:
        await self._client.aclose()
