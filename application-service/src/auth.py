"""Application-service internal-plane auth: direct trusts the upstream-asserted
identity; gateway delegates to the enterprise package.

The service has exactly ONE inbound channel: the gateway. There is no host-proxy
exemption and no direct user credential intake.

direct (community / standalone): the gateway terminates credentials and injects
the `X-App-*` identity headers; this path trusts them. Credentials arriving here
are neither verified nor consumed; the security boundary is the NetworkPolicy
admitting only the gateway pod. A stray bearer without identity headers fails
the header parse -> 401 "invalid principal headers"; actor is required on every
non-public path (fail-closed).
gateway (enterprise): policy implemented in the private enterprise-extensions
package (enterprise_ext.app.AppGatewayAuth, loaded lazily and delegated through
_load_gateway_auth; a missing package raises RuntimeError - loud misconfiguration
rather than silent downgrade; open-source builds cannot install it, so
AUTH_MODE=gateway is a configuration error there). Internal token verification
and ACL rule checks are performed by that handler; ACL rules come from the
shared Redis `acl:rules` key (see AppAuthConfig.redis). Non-bearer requests are
rejected by the handler itself (its legacy-header set is empty), so the
single-channel rule holds in both modes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api.dependencies import Principal, principal_from_headers
from .api.schemas.common import fail
from .i18n import resolve_locale, translate
from .request_logging import log_request_failure, safe_failure_detail
from .trace import TRACE_ID_HEADER

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = frozenset(
    {
        "/internal/v1/health/live",
        "/internal/v1/health/ready",
    }
)


@dataclass
class AppAuthConfig:
    auth_mode: str = "direct"
    service_name: str = "application-service"
    jwks_url: str | None = None
    # ACL rule source for gateway mode: a redis.asyncio client, or a zero-arg
    # async callable returning one (main.py wires runtime.redis.client lazily so
    # no Redis connection is made at app build time). None -> no rules loaded
    # (fail-safe: every request denied). Consumed by the gateway handler only.
    redis: object | None = None


def is_public_path(path: str, method: str) -> bool:
    del method
    return path in _PUBLIC_PATHS


def _load_gateway_auth(config: AppAuthConfig):
    """gateway-mode auth handler: private enterprise-extensions package, lazily loaded.

    Missing package -> RuntimeError (loud startup failure, no silent downgrade -
    fail-closed semantics; open-source builds cannot install it, so
    APPLICATION_SERVICE_AUTH_MODE=gateway is a configuration error there).
    """
    try:
        from enterprise_ext.app import AppGatewayAuth
    except ImportError as exc:
        raise RuntimeError(
            "APPLICATION_SERVICE_AUTH_MODE=gateway requires the private "
            "'enterprise-extensions' package (not installed in open-source builds)"
        ) from exc
    return AppGatewayAuth(config)


def _auth_error(
    request: Request,
    status_code: int,
    detail: str,
    *,
    exception: BaseException | None = None,
) -> JSONResponse:
    """Record early rejections without changing authentication decisions."""

    language = resolve_locale(
        explicit=request.query_params.get("lang"),
        accepted=request.headers.get("Accept-Language"),
    )
    message = translate("errors.common.unauthorized", language)
    log_request_failure(
        request,
        status_code=status_code,
        response_code=status_code,
        error_code="AUTHENTICATION_REJECTED",
        message=safe_failure_detail(detail, "Authentication rejected"),
        exception=exception,
    )
    return JSONResponse(
        status_code=status_code,
        content=fail(code=status_code, msg=message, error=detail),
        headers={TRACE_ID_HEADER: request.state.trace_id},
    )


class AppAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, app_auth: AppAuthConfig, gateway=None) -> None:
        super().__init__(app)
        self._app_auth = app_auth
        # gateway channel verification/ACL assembly lives in the enterprise
        # handler; direct needs no verifier — credentials are terminated
        # upstream. main.py builds the handler once and shares the instance
        # with the lifespan (which owns its start/stop); without it the lazy
        # load below preserves the original construction timing, including the
        # fail-closed RuntimeError when the package is missing.
        self._gateway = gateway
        if self._gateway is None and app_auth.auth_mode == "gateway":
            self._gateway = _load_gateway_auth(app_auth)

    async def dispatch(self, request: Request, call_next):
        if is_public_path(request.url.path, request.method):
            return await call_next(request)
        if self._gateway is not None:
            return await self._gateway_dispatch(request, call_next)
        # direct: the only path trusts the X-App-* identity headers injected by
        # the gateway after terminating credentials. Credentials are neither
        # verified nor consumed here; the boundary is the NetworkPolicy.
        try:
            principal = principal_from_headers(request)
        except HTTPException as exc:
            return _auth_error(request, 401, "invalid principal headers", exception=exc)
        request.state.principal = principal
        return await call_next(request)

    async def _gateway_dispatch(self, request: Request, call_next):
        """Channel decision delegated to enterprise_ext.app.AppGatewayAuth."""

        try:
            ctx = await self._gateway.authenticate(request)
        except HTTPException as exc:
            return _auth_error(request, exc.status_code, str(exc.detail), exception=exc)
        except Exception as exc:
            return _auth_error(request, 401, "invalid token", exception=exc)
        if ctx is None:
            # Single inbound channel: the handler must never return a pass-through
            # here (its legacy-header set is empty); fail closed if one appears.
            return _auth_error(request, 401, "invalid token claims")
        # Empty strings are a valid wire form for absent optional claims (the
        # interpreter defaults a missing workspace_id to ""); normalize them and
        # fail closed if the remaining identity claims cannot form a Principal.
        try:
            principal = Principal(
                actor_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id or None,
            )
        except ValidationError as exc:
            return _auth_error(request, 401, "invalid token claims", exception=exc)
        request.state.principal = principal
        return await call_next(request)


def build_app_auth_middleware(
    app, app_auth: AppAuthConfig, gateway=None
) -> AppAuthMiddleware:
    return AppAuthMiddleware(app, app_auth, gateway=gateway)


__all__ = [
    "AppAuthConfig",
    "AppAuthMiddleware",
    "build_app_auth_middleware",
    "is_public_path",
]
