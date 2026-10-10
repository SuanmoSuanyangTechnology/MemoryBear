"""Model-service internal-plane auth: direct built-in (community) / gateway enterprise extension.

direct: a caller bearer JWT is verified locally against the shared HS256 secret
(MODEL_SERVICE_SECRET, same value as the monolith's SECRET_KEY) and its claims
are authoritative; requests without a bearer fall back to trusting the
`X-Model-*` internal headers injected by the host proxy (channel-2 semantics,
NetworkPolicy as the network-level safety net, only the host api pod is
admitted); missing credentials -> 401 fail-closed.
gateway: enterprise policy implemented in the private enterprise-extensions
package (enterprise_ext.model.ModelGatewayAuth, loaded lazily and delegated
through _load_gateway_auth; a missing package raises RuntimeError - loud
misconfiguration rather than silent downgrade; open-source builds cannot
install it, so AUTH_MODE=gateway is a configuration error there). Internal
token verification and ACL rule checks are performed by that handler; ACL
rules come from the shared Redis `acl:rules` key (see ModelAuthConfig.redis).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from auth_sdk.token import TokenVerifier
from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api.dependencies import (
    InvokePrincipal,
    Principal,
    invoke_principal_from_headers,
    principal_from_headers,
)
from .api.schemas.common import fail
from .errors import BizCode
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

# 运行面（invoke）放行无用户身份的调用方（Celery worker / 后台任务）；管理面 actor 一律必填。
# validate 为只读探测（不落库、无用量归属），宿主 SSO 等外部签名调用方没有用户身份，同组放行；
# 该路径后续若引入写路径，须移出本集合重新收紧。
_ACTOR_OPTIONAL_PATHS = frozenset({"/internal/v1/invoke", "/internal/v1/models/validate"})


def _parse_principal(request: Request) -> Principal | InvokePrincipal:
    """运行面按路径放行无 actor 主体；其余路径 actor 必填（fail-closed）。"""

    path = request.url.path.rstrip("/") or "/"
    if path in _ACTOR_OPTIONAL_PATHS:
        return invoke_principal_from_headers(request)
    return principal_from_headers(request)


@dataclass
class ModelAuthConfig:
    auth_mode: str = "direct"
    service_name: str = "model-service"
    jwks_url: str | None = None
    # Community direct mode: HS256 secret for local bearer-JWT verification
    # (same value as the monolith's SECRET_KEY). Unused in gateway mode.
    secret: str | None = None
    # ACL rule source for gateway mode: a redis.asyncio client, or a zero-arg
    # async callable returning one (main.py wires runtime.redis.client lazily so
    # no Redis connection is made at app build time). None -> no rules loaded
    # (fail-safe: every request denied). Consumed by the gateway handler only.
    redis: object | None = None


def is_public_path(path: str, method: str) -> bool:
    del method
    return path in _PUBLIC_PATHS


def _load_gateway_auth(config: ModelAuthConfig):
    """gateway 模式认证处理器：私有 enterprise-extensions 惰性加载。

    缺失即 RuntimeError（启动期响亮失败，不静默降级，与 fail-closed 语义一致；
    开源构建装不到该包，AUTH_MODE=gateway 属配置错误）。
    """
    try:
        from enterprise_ext.model import ModelGatewayAuth
    except ImportError as exc:
        raise RuntimeError(
            "AUTH_MODE=gateway requires the private 'enterprise-extensions' "
            "package (not installed in open-source builds)"
        ) from exc
    return ModelGatewayAuth(config)


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


class ModelAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, model_auth: ModelAuthConfig) -> None:
        super().__init__(app)
        self._model_auth = model_auth
        self._gateway = (
            _load_gateway_auth(model_auth) if model_auth.auth_mode == "gateway" else None
        )
        # direct mode (non-gateway): local HS256 verifier for caller bearer JWTs.
        # gateway mode verifies internal tokens inside the enterprise handler.
        if model_auth.auth_mode != "gateway" and model_auth.secret is not None:
            self._verifier: TokenVerifier | None = TokenVerifier(secret=model_auth.secret)
        else:
            self._verifier: TokenVerifier | None = None

    async def dispatch(self, request: Request, call_next):
        if is_public_path(request.url.path, request.method):
            return await call_next(request)
        if self._gateway is not None:
            return await self._gateway_dispatch(request, call_next)
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return await self._bearer_dispatch(request, call_next, auth)
        try:
            principal = _parse_principal(request)
        except HTTPException as exc:
            return _auth_error(request, 401, "invalid principal headers", exception=exc)
        request.state.principal = principal
        return await call_next(request)

    async def _bearer_dispatch(self, request: Request, call_next, auth: str):
        """Local JWT verification (direct mode): bearer claims are authoritative.

        Strict token_type="access" rejects refresh tokens (same secret, longer
        TTL). The principal shape follows the same per-path rule as the header
        path: actor required on the management plane, optional on invoke/validate.
        """
        token = auth.removeprefix("Bearer ").strip()
        if self._verifier is None:
            return _auth_error(request, 500, "auth misconfigured")
        try:
            payload = await self._verifier.verify_jwt(token, token_type="access")
        except Exception as exc:
            return _auth_error(request, 401, "invalid token", exception=exc)
        path = request.url.path.rstrip("/") or "/"
        principal_model = InvokePrincipal if path in _ACTOR_OPTIONAL_PATHS else Principal
        try:
            principal = principal_model.model_validate(
                {
                    "actor_id": payload.get("sub"),
                    "actor_name": None,
                    "tenant_id": payload.get("tenant_id"),
                    "workspace_id": payload.get("workspace_id"),
                    "source": None,
                }
            )
        except ValidationError as exc:
            return _auth_error(request, 401, "invalid token", exception=exc)
        request.state.principal = principal
        return await call_next(request)

    async def _gateway_dispatch(self, request: Request, call_next):
        """通道 1/2 判定委托企业处理器（enterprise_ext.model.ModelGatewayAuth）。"""

        try:
            ctx = await self._gateway.authenticate(request)
        except HTTPException as exc:
            return _auth_error(request, exc.status_code, str(exc.detail), exception=exc)
        except Exception as exc:
            return _auth_error(request, 401, "invalid token", exception=exc)
        if ctx is None:
            # 通道 2：宿主代理直连豁免（过渡态，NetworkPolicy 兜底受信来源）
            return await call_next(request)
        # Empty strings are a valid wire form for absent optional claims (the
        # interpreter defaults a missing workspace_id to ""); normalize them and
        # fail closed if the remaining identity claims cannot form a Principal.
        try:
            principal = Principal(
                actor_id=ctx.user_id,
                actor_name=None,
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id or None,
            )
        except ValidationError as exc:
            return _auth_error(request, 401, "invalid token claims", exception=exc)
        request.state.principal = principal
        return await call_next(request)


def build_model_auth_middleware(app, model_auth: ModelAuthConfig) -> ModelAuthMiddleware:
    return ModelAuthMiddleware(app, model_auth)


__all__ = [
    "ModelAuthConfig",
    "ModelAuthMiddleware",
    "build_model_auth_middleware",
    "is_public_path",
    "BizCode",
]
