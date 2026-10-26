"""Service-to-service Bearer authentication.

The service has no user-facing surface: only the main application calls it, with a
shared secret from ``SANDBOX_API_TOKEN``. A missing or short secret refuses to start
instead of silently serving unauthenticated.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping

from fastapi import Request

from ..config import ConfigError
from ..errors import SandboxError

TOKEN_ENV = "SANDBOX_API_TOKEN"
MIN_TOKEN_LENGTH = 16


def load_api_token(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    token = (source.get(TOKEN_ENV) or "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"{TOKEN_ENV} must be set to a secret of at least {MIN_TOKEN_LENGTH} chars"
        )
    return token


def make_auth_dependency(token: str):
    """Build a FastAPI dependency that checks ``Authorization: Bearer <token>``."""

    expected = token.encode("utf-8")

    async def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.strip().encode("utf-8"), expected
        ):
            raise SandboxError("unauthorized", "missing or invalid service token")

    return require_token
