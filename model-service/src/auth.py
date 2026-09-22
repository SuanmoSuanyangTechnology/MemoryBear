"""模型服务内部面鉴权（D-M7-3）：direct 内置（社区）/ gateway 企业扩展。

direct：信任宿主代理注入的 `X-Model-*` 内部头（通道 2 语义，网络隔离
NetworkPolicy 兜底，只放行宿主 api pod）；缺头即 401 fail-closed。
gateway：企业策略，实现位于私有 enterprise-extensions 包
（enterprise_ext.model.ModelGatewayAuth，经 _load_gateway_auth 惰性加载委托，
缺失即 RuntimeError——misconfiguration 响亮暴露，不静默降级；开源构建装不到
该包，AUTH_MODE=gateway 属配置错误）。M10 收紧批次落地内部 token 验签。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api.dependencies import Principal, principal_from_headers
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


@dataclass
class ModelAuthConfig:
    auth_mode: str = "direct"
    service_name: str = "model-service"
    jwks_url: str | None = None


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

    async def dispatch(self, request: Request, call_next):
        if is_public_path(request.url.path, request.method):
            return await call_next(request)
        if self._gateway is not None:
            return await self._gateway_dispatch(request, call_next)
        try:
            principal = principal_from_headers(request)
        except HTTPException as exc:
            return _auth_error(request, 401, "invalid principal headers", exception=exc)
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
        request.state.principal = Principal(
            actor_id=ctx.user_id,
            actor_name=None,
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
        )
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
