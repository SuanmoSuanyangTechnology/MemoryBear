"""gRPC authentication for the invoke service.

gateway mode: channel-1 internal tokens are verified by the enterprise handler
and claims are the only identity source; channel-2 (no credential) trusts the
``X-Model-*`` metadata under the NetworkPolicy boundary, mirroring HTTP.
direct mode: there is no credential channel at all — metadata is the identity.
"""

from __future__ import annotations

from fastapi import HTTPException
from memorybear_grpc.errors import grpc_status_for_http
from memorybear_grpc.interceptors import Authenticator
from memorybear_grpc.request import MetadataRequest
from pydantic import ValidationError

import grpc

from ..api.dependencies import (
    SOURCE_HEADER,
    InvokePrincipal,
    invoke_principal_from_headers,
)
from ..auth import ModelAuthConfig, _load_gateway_auth

INVOKE_ACL_METHOD = "POST"
INVOKE_ACL_PATH = "/internal/v1/invoke"


def build_authenticator(
    config: ModelAuthConfig, *, gateway: object | None = None
) -> Authenticator:
    """Return the ``AuthInterceptor`` authenticator for this deployment mode."""
    handler = gateway
    if handler is None and config.auth_mode == "gateway":
        handler = _load_gateway_auth(config)

    async def authenticate(context, method: str) -> InvokePrincipal:
        del method  # ACL is keyed by the HTTP-equivalent route, not the rpc path
        request = MetadataRequest(
            context.invocation_metadata(),
            method=INVOKE_ACL_METHOD,
            path=INVOKE_ACL_PATH,
        )
        if handler is None:
            try:
                return invoke_principal_from_headers(request)
            except HTTPException as exc:
                await context.abort(grpc.StatusCode.UNAUTHENTICATED, str(exc.detail))
        try:
            claims = await handler.authenticate(request)
        except HTTPException as exc:
            await context.abort(grpc_status_for_http(exc.status_code), str(exc.detail))
        except Exception:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid token")
        if claims is None:
            # Channel 2: trusted attribution headers, never a user credential.
            try:
                return invoke_principal_from_headers(request)
            except HTTPException as exc:
                await context.abort(grpc.StatusCode.UNAUTHENTICATED, str(exc.detail))
        try:
            return InvokePrincipal(
                actor_id=claims.user_id,
                actor_name=None,
                tenant_id=claims.tenant_id,
                workspace_id=claims.workspace_id or None,
                source=request.headers.get(SOURCE_HEADER),
            )
        except ValidationError:
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "invalid token claims"
            )

    return authenticate


__all__ = [
    "INVOKE_ACL_METHOD",
    "INVOKE_ACL_PATH",
    "build_authenticator",
]
