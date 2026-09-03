"""kb 鉴权（评审稿 4.3）：direct 内置（社区版，独立部署自验）/ gateway 企业扩展。

direct：JWT 本地验签（secret）或 API key 走 identity 集中校验（评审稿 4.5.4
fail-closed，x-api-key 无 identity 配置即拒）——社区单机部署无需网关也能独立鉴权。
gateway：通道 1/2 判定是企业策略，实现位于私有 enterprise-extensions 包
（enterprise_ext.kb.KbGatewayAuth，经 _load_gateway_auth 惰性加载委托，缺失即
RuntimeError——misconfiguration 响亮暴露，不静默降级，与 fail-closed 语义一致；
开源构建装不到该包，AUTH_MODE=gateway 属配置错误）。企业/开源拆分规范见外层仓库。

安全要点：通道 2 豁免依赖 NetworkPolicy 网络隔离兜底（只放行网关 + 老单体 pod）；
AUTH_MODE=gateway 时无凭据即拒（fail-closed）；应急开关仅限灰度窗口期使用。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from auth_sdk.api_key import ApiKeyVerifier, ApiKeyVerifyUnavailable
from auth_sdk.schema import UserContext
from auth_sdk.token import TokenVerifier
from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .api.dependencies import Principal

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {
    "/internal/v1/health/live",
    "/internal/v1/health/ready",
    "/internal/v1/chunks/retrieve_type",
    "/internal/v1/knowledges/knowledgetype",
    "/internal/v1/knowledges/permissiontype",
    "/internal/v1/knowledges/parsertype",
}


@dataclass
class KbAuthConfig:
    # direct 社区默认（独立部署无需企业包）；gateway 需 enterprise-extensions，缺失即 RuntimeError
    auth_mode: str = "direct"
    service_name: str = "kb"
    kill_switch_file: str | None = None
    jwks_url: str | None = None
    secret: str | None = None
    # ACL 规则来源：redis.asyncio 客户端，或返回该客户端的异步零参 callable（kb 用
    # runtime.redis.client 惰性接入，避免在 app 构建期连 redis）。None 时不加载规则。
    # 仅 gateway 模式（企业处理器）消费；direct 模式不用 Redis。
    redis: object | None = None
    # direct 模式 API key 集中校验端点（identity POST /internal/api-key-verify，评审焦点
    # #16 方案 A）。None（社区单机部署无 identity）时 x-api-key 请求 fail-closed 拒绝。
    api_key_verify_url: str | None = None
    # httpx client 注入（测试用 MockTransport；None 时 verifier 自建）
    api_key_client: object | None = None


def is_public_path(path: str, method: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    # 公开例外仅限精确单文件下载 GET /internal/v1/files/{uuid}（浏览器 <img> 渲染
    # 无鉴权头，评审稿 4.3.4；身份由上游网关/老单体保证）。必须精确匹配整段路径：
    # 列表等其余 /files/* 路由若公开，攻击者可伪造 X-KB-* 头跨工作区拉取文件。
    if (method == "GET" and re.fullmatch(
            r"/internal/v1/files/[0-9a-fA-F-]{36}", path) is not None):
        return True
    return False


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


class KbAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, kb_auth: KbAuthConfig) -> None:
        super().__init__(app)
        self._kb_auth = kb_auth
        # direct 模式（非 gateway）：JWT 本地验签 verifier（HS256，社区版）。
        # gateway 模式的通道 1 验签/ACL 装配在企业处理器内（enterprise_ext.kb）。
        if kb_auth.auth_mode != "gateway" and kb_auth.secret is not None:
            self._verifier: TokenVerifier | None = TokenVerifier(secret=kb_auth.secret)
        else:
            self._verifier: TokenVerifier | None = None
        self._api_key_verifier: ApiKeyVerifier | None = (
            ApiKeyVerifier(verify_url=kb_auth.api_key_verify_url,
                           client=kb_auth.api_key_client)
            if kb_auth.api_key_verify_url
            else None
        )
        self._gateway = _load_gateway_auth(kb_auth) if kb_auth.auth_mode == "gateway" else None

    async def dispatch(self, request: Request, call_next):
        # 应急开关：文件存在即恢复无鉴权状态（仅灰度窗口期，评审稿 6.2）
        if self._kill_switch_active():
            logger.warning("kb auth kill switch ACTIVE — bypassing auth")
            return await call_next(request)
        path = request.url.path
        if is_public_path(path, request.method):
            return await call_next(request)
        if self._kb_auth.auth_mode == "gateway":
            return await self._gateway_dispatch(request, call_next)
        # direct（社区版）：不读 X-KB-* 头；JWT 本地验签 / API key 走 identity 集中
        # 校验（评审稿 4.5.4 fail-closed）
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ").strip()
            if self._verifier is None:
                return JSONResponse(status_code=500, content={"detail": "auth misconfigured"})
            try:
                payload = await self._verifier.verify_jwt(token)
            except Exception:
                return JSONResponse(status_code=401, content={"detail": "invalid token"})
            try:
                request.state.principal = Principal(
                    actor_id=payload.get("sub"),
                    actor_name=None,
                    tenant_id=payload.get("tenant_id"),
                    workspace_id=payload.get("workspace_id"),
                )
            except ValidationError as exc:
                # sub/tenant/workspace 非 UUID（external 用户 token 无租户语境）：
                # 无法映射 kb 身份 → fail-closed
                logger.warning("direct jwt principal invalid, rejecting: %s", exc)
                return JSONResponse(status_code=401, content={"detail": "invalid token"})
            return await call_next(request)
        api_key = request.headers.get("x-api-key")
        if api_key is not None:
            if self._api_key_verifier is None:
                return JSONResponse(status_code=500, content={"detail": "auth misconfigured"})
            try:
                claims = await self._api_key_verifier.verify(api_key)
            except ApiKeyVerifyUnavailable:
                return JSONResponse(status_code=401, content={"detail": "auth unavailable"})
            if claims is None:
                return JSONResponse(status_code=401, content={"detail": "invalid api key"})
            try:
                request.state.principal = Principal(
                    actor_id=claims.get("api_key_id"), actor_name=None,
                    tenant_id=claims.get("tenant_id"), workspace_id=claims.get("workspace_id"))
            except ValidationError as exc:
                # identity claims 非 UUID/缺字段：无法映射 kb 身份 → fail-closed
                logger.warning("direct api key claims invalid, rejecting: %s", exc)
                return JSONResponse(status_code=401, content={"detail": "invalid api key"})
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "missing credentials"})

    async def _gateway_dispatch(self, request: Request, call_next):
        """通道 1/2 判定委托企业处理器（判定逻辑在 enterprise_ext.kb.KbGatewayAuth）。

        authenticate 返回 UserContext（主体，映射 Principal）或 None（通道 2 老单体
        豁免透传）；HTTPException = 拒绝（401/403/500，SDK 语义原样转 JSONResponse）。
        """
        try:
            ctx: UserContext | None = await self._gateway.authenticate(request)
        except HTTPException as exc:
            # 验签失败 401 / ACL 拒绝 403 / 配置缺失 500，按 SDK 语义原样返回
            logger.warning("channel1 auth denied status=%s detail=%s", exc.status_code, exc.detail)
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        except Exception as exc:
            logger.warning("channel1 auth failed: %s", exc)
            return JSONResponse(status_code=401, content={"detail": "invalid token"})
        if ctx is None:
            # 通道 2：老单体直连豁免（过渡态，NetworkPolicy 兜底受信来源）
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
            logger.warning("channel1 principal invalid, rejecting: %s", exc)
            return JSONResponse(status_code=401, content={"detail": "invalid token"})
        return await call_next(request)

    def _kill_switch_active(self) -> bool:
        path = self._kb_auth.kill_switch_file
        if not path:
            return False
        return os.path.exists(path)


def build_kb_auth_middleware(app, kb_auth: KbAuthConfig) -> KbAuthMiddleware:
    return KbAuthMiddleware(app, kb_auth)


__all__ = ["KbAuthConfig", "KbAuthMiddleware", "build_kb_auth_middleware", "is_public_path"]
