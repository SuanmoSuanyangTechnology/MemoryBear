from __future__ import annotations

import hmac
import re
from dataclasses import dataclass

import httpx
from auth_sdk.token import TokenVerifier
from fastapi import Request
from jose import JWTError

from .config import settings
from .errors import BizCode, BusinessException

_EXEMPT = frozenset({"/internal/v1/health/live", "/internal/v1/health/ready",
                     "/internal/v1/metrics", "/openapi.json"})
_INTERNAL_PREFIX = "/internal/v1/files"
_STORAGE_PREFIX = "/internal/v1/storage"
_TOKEN_HEADER = "X-File-Service-Token"
_UUID = r"[0-9a-fA-F-]{36}"
# Public reads mirror the monolith's unauthenticated storage routes
# (file_storage_controller.py); gateway task 2b holds the same pattern set.
_PUBLIC_STORAGE_PATTERNS = (
    re.compile(r"/internal/v1/storage/files/info-by-url"),
    re.compile(rf"/internal/v1/storage/files/{_UUID}/(?:url|public-url|status)"),
    re.compile(rf"/internal/v1/storage/(?:public|permanent)/{_UUID}"),
)


def _is_public_storage(path: str, method: str) -> bool:
    return method == "GET" and any(
        p.fullmatch(path) for p in _PUBLIC_STORAGE_PATTERNS)


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    workspace_id: str | None


async def _jwks() -> list[dict]:
    async with httpx.AsyncClient(timeout=3.0) as client:
        resp = await client.get(settings.identity_jwks_url)
        resp.raise_for_status()
        return resp.json()["keys"]


_verifier: TokenVerifier | None = None


def _get_verifier() -> TokenVerifier:
    global _verifier
    if _verifier is None:
        _verifier = TokenVerifier(jwks_fetch=_jwks)
    return _verifier


def _unauthorized(msg: str) -> BusinessException:
    return BusinessException(msg, BizCode.UNAUTHORIZED, 401)


async def authenticate(request: Request) -> None:
    path = request.url.path
    if path in _EXEMPT:
        return
    if path.startswith(_STORAGE_PREFIX) and _is_public_storage(path, request.method):
        return
    if path.startswith(_INTERNAL_PREFIX):
        supplied = request.headers.get(_TOKEN_HEADER, "")
        expected = settings.file_service_internal_token.get_secret_value()
        # Fail closed: an unconfigured token must never admit an empty header.
        if not expected or not hmac.compare_digest(supplied, expected):
            raise _unauthorized("invalid service token")
        request.state.principal = Principal(
            user_id=request.headers.get("X-Actor-Id", ""),
            tenant_id=request.headers.get("X-Tenant-Id", ""),
            workspace_id=request.headers.get("X-Workspace-Id") or None,
        )
        if not request.state.principal.tenant_id:
            raise _unauthorized("missing tenant context")
        return
    if not path.startswith(_STORAGE_PREFIX):
        return
    if settings.auth_mode == "gateway":
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise _unauthorized("missing internal token")
        try:
            payload = await _get_verifier().verify_jwt(
                auth[7:], expected_aud="file-service")
        except (ValueError, JWTError):
            raise _unauthorized("invalid internal token")
        if not payload.get("sub") or not payload.get("tenant_id"):
            raise _unauthorized("invalid token claims")
        principal = Principal(payload["sub"], payload["tenant_id"],
                              payload.get("workspace_id"))
        for header, claim in (("X-User-Id", "sub"), ("X-Tenant-Id", "tenant_id")):
            if request.headers.get(header) and request.headers[header] != payload.get(claim):
                raise _unauthorized("identity header/token mismatch")
        request.state.principal = principal
        return
    tenant = request.headers.get("X-Tenant-Id")
    user = request.headers.get("X-User-Id")
    if not tenant or not user:
        raise _unauthorized("missing identity headers")
    request.state.principal = Principal(user, tenant,
                                        request.headers.get("X-Workspace-Id") or None)
