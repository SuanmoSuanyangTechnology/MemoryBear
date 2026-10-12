"""kb authentication: direct trusts the upstream-asserted identity; gateway
delegates the channel 1/2 judgment to the enterprise package.

direct (community / standalone): the only path trusts the X-KB-* identity
headers — upstream terminates credentials before requests reach kb (the gateway
in split deployments, the monolith in legacy wiring). Credentials arriving here
are neither verified nor consumed; the security boundary is the NetworkPolicy
allowing only gateway + monolith pods (the same fallback channel 2 relies on).
gateway: the judgment lives in the private enterprise-extensions package
(enterprise_ext.kb.KbGatewayAuth, lazily loaded via _load_gateway_auth;
missing → loud RuntimeError, never a silent downgrade to direct — open-source
builds cannot install it, so AUTH_MODE=gateway is a misconfiguration there).

Fail-closed: no resolvable identity headers → 401 "invalid principal headers".
The kill switch remains for grayscale rollback windows only.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from auth_sdk.schema import UserContext
from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api.dependencies import Principal, _principal_from_headers
from .errors import KnowledgeError
from .request_logging import log_request_failure, safe_failure_detail
from .trace import TRACE_ID_HEADER

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {
    "/internal/v1/health/live",
    "/internal/v1/health/ready",
    "/internal/v1/chunks/retrieve_type",
    "/internal/v1/knowledges/knowledgetype",
    "/internal/v1/knowledges/permissiontype",
    "/internal/v1/knowledges/parsertype",
}

# 公开例外仅限精确单文件下载 GET /internal/v1/files/{uuid}（浏览器 <img> 渲染
# 无鉴权头，评审稿 4.3.4）。必须精确匹配整段路径：列表等其余 /files/* 路由若公开，
# 攻击者可伪造 X-KB-* 头跨工作区拉取文件。
_SINGLE_FILE_DOWNLOAD_RE = re.compile(r"/internal/v1/files/[0-9a-fA-F-]{36}")


@dataclass
class KbAuthConfig:
    # direct is the community default (standalone deployment, no enterprise
    # package needed); gateway requires enterprise-extensions, missing → RuntimeError
    auth_mode: str = "direct"
    service_name: str = "kb"
    kill_switch_file: str | None = None
    jwks_url: str | None = None
    # ACL rule source: a redis.asyncio client, or an async zero-arg callable
    # returning one (kb wires runtime.redis.client lazily, avoiding a Redis
    # connection at app-build time). None → no rules loaded. Consumed by the
    # gateway handler only; direct mode does not use Redis.
    redis: object | None = None


def _is_single_file_download(path: str, method: str) -> bool:
    return method == "GET" and _SINGLE_FILE_DOWNLOAD_RE.fullmatch(path) is not None


def _has_credentials(request: Request) -> bool:
    """请求是否携带可验证凭据（Bearer 内部 token/JWT 或 API key）。"""
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") or request.headers.get("x-api-key") is not None


def _has_identity_headers(request: Request) -> bool:
    """Any injected kb identity header present (gateway direct mode injects the
    X-KB-* trio after terminating credentials upstream)."""
    return any(request.headers.get(name) is not None for name in (
        "X-KB-Actor-ID", "X-KB-Tenant-ID", "X-KB-Workspace-ID"))


def is_public_path(path: str, method: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return _is_single_file_download(path, method)


def _load_gateway_auth(kb_auth: KbAuthConfig):
    """gateway 模式认证处理器：私有 enterprise-extensions 惰性加载。

    缺失即 RuntimeError（启动期响亮失败，不静默降级，与 fail-closed 语义一致；
    开源构建装不到该包，AUTH_MODE=gateway 属配置错误）。
    """
    try:
        from enterprise_ext.kb import KbGatewayAuth
    except ImportError as exc:
        raise RuntimeError(
            "AUTH_MODE=gateway requires the private 'enterprise-extensions' "
            "package (not installed in open-source builds)") from exc
    return KbGatewayAuth(kb_auth)


def _auth_error(
    request: Request, status_code: int, detail: object, *, exception: BaseException | None = None,
) -> JSONResponse:
    """Record early rejections without changing authentication decisions."""
    log_request_failure(
        request, status_code=status_code, response_code=None,
        error_code="AUTHENTICATION_REJECTED",
        message=safe_failure_detail(detail, "Authentication rejected"),
        exception=exception,
        validation_errors=exception.errors() if isinstance(exception, ValidationError) else None,
    )
    return JSONResponse(
        status_code=status_code, content={"detail": detail},
        headers={TRACE_ID_HEADER: request.state.trace_id},
    )


class KbAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, kb_auth: KbAuthConfig) -> None:
        super().__init__(app)
        self._kb_auth = kb_auth
        # gateway channel-1 verification/ACL assembly lives in the enterprise
        # handler (enterprise_ext.kb); direct needs no verifier — credentials
        # are terminated upstream.
        self._gateway = _load_gateway_auth(kb_auth) if kb_auth.auth_mode == "gateway" else None

    async def dispatch(self, request: Request, call_next):
        request.state.kb_legacy_proxy_authenticated = False
        # 应急开关：文件存在即恢复无鉴权状态（仅灰度窗口期，评审稿 6.2）
        if self._kill_switch_active():
            logger.warning("kb auth kill switch ACTIVE — bypassing auth")
            return await call_next(request)
        path = request.url.path
        if is_public_path(path, request.method):
            # Single-file download exemption: only requests with neither
            # credentials nor identity headers short-circuit (browser <img>
            # rendering / the monolith's public-download proxy sends only the
            # X-KB-Source header). Requests carrying credentials (channel-1
            # internal token) must run the gateway dispatch; requests carrying
            # the injected X-KB-* headers (gateway direct mode) must run the
            # trust path — letting them through would leave principal=None with
            # source=GENERAL → 400 KB_PRINCIPAL_INVALID.
            if (not _is_single_file_download(path, request.method)
                    or (not _has_credentials(request) and not _has_identity_headers(request))):
                return await call_next(request)
        if self._kb_auth.auth_mode == "gateway":
            return await self._gateway_dispatch(request, call_next)
        # direct: the only path trusts the X-KB-* identity headers injected by
        # the upstream (gateway after terminating credentials / monolith direct
        # wiring). Credentials are neither verified nor consumed here — the
        # gateway already terminated them; the boundary is the NetworkPolicy.
        try:
            request.state.principal = _principal_from_headers(request)
        except KnowledgeError as exc:
            return _auth_error(request, 401, "invalid principal headers", exception=exc)
        request.state.kb_legacy_proxy_authenticated = True
        return await call_next(request)

    async def _gateway_dispatch(self, request: Request, call_next):
        """通道 1/2 判定委托企业处理器（判定逻辑在 enterprise_ext.kb.KbGatewayAuth）。

        authenticate 返回 UserContext（主体，映射 Principal）或 None（通道 2 老单体
        豁免透传）；HTTPException = 拒绝（401/403/500，SDK 语义原样转 JSONResponse）。
        """
        try:
            ctx: UserContext | None = await self._gateway.authenticate(request)
        except HTTPException as exc:
            # 验签失败 401 / ACL 拒绝 403 / 配置缺失 500，按 SDK 语义原样返回
            return _auth_error(request, exc.status_code, exc.detail, exception=exc)
        except Exception as exc:
            return _auth_error(request, 401, "invalid token", exception=exc)
        if ctx is None:
            # 通道 2：老单体直连豁免（过渡态，NetworkPolicy 兜底受信来源）
            request.state.kb_legacy_proxy_authenticated = True
            return await call_next(request)
        try:
            request.state.principal = Principal(
                actor_id=ctx.user_id,
                actor_name=None,  # claims 不携带 actor_name（老 identity 头在通道 1 下被忽略）
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        except ValidationError as exc:
            # sub 非用户 UUID（如 ak:* API Key token）：无法映射 kb 用户身份 → fail-closed
            return _auth_error(request, 401, "invalid token", exception=exc)
        return await call_next(request)

    def _kill_switch_active(self) -> bool:
        path = self._kb_auth.kill_switch_file
        if not path:
            return False
        return os.path.exists(path)


def build_kb_auth_middleware(app, kb_auth: KbAuthConfig) -> KbAuthMiddleware:
    return KbAuthMiddleware(app, kb_auth)


__all__ = ["KbAuthConfig", "KbAuthMiddleware", "build_kb_auth_middleware", "is_public_path"]
