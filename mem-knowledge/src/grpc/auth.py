"""gRPC authentication for the knowledge retrieval service.

direct mode: the gateway terminates credentials upstream and injects the
``X-KB-*`` identity metadata; metadata is the identity and the boundary is the
NetworkPolicy, mirroring :mod:`src.auth`.
gateway mode: channel-1 internal tokens are verified by the enterprise handler
and its claims are authoritative; channel 2 (no credential) trusts the
``X-KB-*`` metadata, exactly like the HTTP middleware.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from memorybear_grpc.interceptors import Authenticator
from memorybear_grpc.request import MetadataRequest
from pydantic import ValidationError

from ..api.dependencies import Principal, _principal_from_headers
from ..auth import KbAuthConfig, _load_gateway_auth
from ..errors import KnowledgeError
from ..i18n import resolve_locale
from ..trace import get_trace_id, normalize_trace_id
from .envelope import abort_with_status

RETRIEVAL_ACL_PATH = "/internal/v1/chunks/retrieval"
POLICY_ACL_PATH = "/internal/v1/chunks/retrieval-policy"

Reject = Callable[[int, str], Awaitable[None]]


def _acl_path(method: str) -> str:
    return POLICY_ACL_PATH if method.rsplit("/", 1)[-1] == "RetrievalPolicy" else RETRIEVAL_ACL_PATH


def _kill_switch_active(config: KbAuthConfig) -> bool:
    path = config.kill_switch_file
    return bool(path) and os.path.exists(path)


def _trace_id() -> str:
    return get_trace_id() or normalize_trace_id(None)


async def _principal_or_reject(request: MetadataRequest, reject: Reject) -> Principal:
    try:
        return _principal_from_headers(request)
    except KnowledgeError as exc:
        await reject(401, "invalid principal headers")
        raise AssertionError("grpc auth reject returned") from exc


def build_authenticator(
    config: KbAuthConfig, *, gateway: object | None = None
) -> Authenticator:
    """Return the ``AuthInterceptor`` authenticator for this deployment mode."""

    handler = gateway
    if handler is None and config.auth_mode == "gateway":
        handler = _load_gateway_auth(config)

    async def authenticate(context, method: str) -> Principal:
        request = MetadataRequest(
            context.invocation_metadata(),
            method="POST",
            path=_acl_path(method),
        )
        language = resolve_locale(
            explicit=request.headers.get("x-language-type"),
            accepted=request.headers.get("accept-language"),
        )

        async def reject(http_status: int, detail: str) -> None:
            await abort_with_status(
                context,
                http_status,
                detail,
                language=language,
                trace_id=_trace_id(),
            )

        # Emergency rollback window only: the same file bypasses HTTP auth.
        if _kill_switch_active(config):
            return await _principal_or_reject(request, reject)
        if handler is None:
            return await _principal_or_reject(request, reject)
        try:
            claims = await handler.authenticate(request)
        except HTTPException as exc:
            await reject(exc.status_code, str(exc.detail))
            raise
        except Exception:
            await reject(401, "invalid token")
            raise
        if claims is None:
            # Channel 2: trusted attribution metadata, never a user credential.
            return await _principal_or_reject(request, reject)
        try:
            return Principal(
                actor_id=claims.user_id,
                actor_name=None,
                tenant_id=claims.tenant_id,
                workspace_id=claims.workspace_id,
            )
        except ValidationError as exc:
            await reject(401, "invalid token claims")
            raise AssertionError("grpc auth reject returned") from exc

    return authenticate


__all__ = [
    "POLICY_ACL_PATH",
    "RETRIEVAL_ACL_PATH",
    "build_authenticator",
]