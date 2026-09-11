"""memory-profile 鉴权（对齐 mem-knowledge 评审稿 4.3 双模式）。

direct：JWT 本地验签（secret）或 API key 走 identity 集中校验（fail-closed，
x-api-key 无 identity 配置即拒）——社区单机部署无需网关也能独立鉴权。
gateway：内部 token 验签 + ACL 判定是企业策略，实现位于私有 enterprise-extensions 包
（enterprise_ext.memory_profile.MemoryProfileGatewayAuth，经 _load_gateway_auth 惰性
加载委托，缺失即 RuntimeError——misconfiguration 响亮暴露，不静默降级，与 fail-closed
语义一致；开源构建装不到该包，MEMORY_AUTH_MODE=gateway 属配置错误）。

与 kb 的差异：memory-profile 在老单体是 API-key 鉴权、无 X-KB-* 身份头直连，故
gateway 模式只保留通道 1（内部 token 验签 + ACL），无「通道 2 老单体直连豁免」。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from uuid import UUID

from auth_sdk.api_key import ApiKeyVerifier, ApiKeyVerifyUnavailable
from auth_sdk.schema import UserContext
from auth_sdk.token import TokenVerifier
from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {
    "/docs",
    "/openapi.json",
    "/health/ready",
}


class Principal(BaseModel):
    """验签后的调用方身份（由中间件写入 request.state.principal）。"""

    actor_id: UUID
    actor_name: str | None = None
    tenant_id: UUID
    workspace_id: UUID


@dataclass
class MemoryProfileAuthConfig:
    # direct 社区默认（独立部署无需企业包）；gateway 需 enterprise-extensions，缺失即 RuntimeError
    auth_mode: str = "direct"
    service_name: str = "memory"
    kill_switch_file: str | None = None
    jwks_url: str | None = None
    secret: str | None = None
    # ACL 规则来源：redis.asyncio 客户端，或返回该客户端的异步零参 callable（本服务用
    # infrastructure.redis.get_redis 惰性接入，避免 app 构建期连 redis）。None 时不加载规则。
    # 仅 gateway 模式（企业处理器）消费；direct 模式不用 Redis。
    redis: object | None = None
    # direct 模式 API key 集中校验端点（identity POST /internal/api-key-verify）。
    # None（社区单机部署无 identity）时 x-api-key 请求 fail-closed 拒绝。
    api_key_verify_url: str | None = None
    # httpx client 注入（测试用 MockTransport；None 时 verifier 自建）
    api_key_client: object | None = None


def is_public_path(path: str, method: str) -> bool:
    return path in _PUBLIC_PATHS


async def get_principal(request: Request) -> Principal:
    """身份依赖：返回中间件验签后写入 request.state.principal 的主体。

    请求经过 MemoryProfileAuthMiddleware 后 principal 必已写入；供路由层
    Depends(get_principal) 注入。未写入（理论不可达）→ fail-closed 401。
    """
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    raise HTTPException(status_code=401, detail="unauthenticated")


async def get_optional_principal(request: Request) -> Principal | None:
    """可选身份依赖：有验签主体返回 Principal，否则返回 None（可选鉴权场景）。"""
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    return None


def _load_gateway_auth(config: MemoryProfileAuthConfig):
    """gateway 模式认证处理器：私有 enterprise-extensions 惰性加载。

    缺失即 RuntimeError（启动期响亮失败，不静默降级，与 fail-closed 语义一致；
    开源构建装不到该包，MEMORY_AUTH_MODE=gateway 属配置错误）。
    """
    try:
        from enterprise_ext.memory_profile import MemoryProfileGatewayAuth
    except ImportError as exc:
        raise RuntimeError(
            "MEMORY_AUTH_MODE=gateway requires the private 'enterprise-extensions' "
            "package (not installed in open-source builds)") from exc
    return MemoryProfileGatewayAuth(config)


class MemoryProfileAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: MemoryProfileAuthConfig) -> None:
        super().__init__(app)
        self._config = config
        # direct 模式（非 gateway）：JWT 本地验签 verifier（HS256，社区版）。
        # gateway 模式的通道 1 验签/ACL 装配在企业处理器内（enterprise_ext.memory_profile）。
        if config.auth_mode != "gateway" and config.secret is not None:
            self._verifier: TokenVerifier | None = TokenVerifier(secret=config.secret)
        else:
            self._verifier: TokenVerifier | None = None
        self._api_key_verifier: ApiKeyVerifier | None = (
            ApiKeyVerifier(verify_url=config.api_key_verify_url,
                           client=config.api_key_client)
            if config.api_key_verify_url
            else None
        )
        self._gateway = _load_gateway_auth(config) if config.auth_mode == "gateway" else None

    async def dispatch(self, request: Request, call_next):
        # 应急开关：文件存在即恢复无鉴权状态（仅灰度窗口期）
        if self._kill_switch_active():
            logger.warning("memory-profile auth kill switch ACTIVE — bypassing auth")
            return await call_next(request)
        path = request.url.path
        if is_public_path(path, request.method):
            return await call_next(request)
        if self._config.auth_mode == "gateway":
            return await self._gateway_dispatch(request, call_next)
        # direct（社区版）：JWT 本地验签 / API key 走 identity 集中校验（fail-closed）
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ").strip()
            if self._verifier is None:
                return JSONResponse(status_code=500, content={"detail": "auth misconfigured"})
            try:
                # type 强校验=access：防 refresh token（同 SECRET_KEY、TTL 更长）访问
                # 业务端点，对齐老单体 verify_token 默认语义与网关用户路径。
                payload = await self._verifier.verify_jwt(token, token_type="access")
            except Exception as e:
                logger.error(e)
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
                # 无法映射身份 → fail-closed
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
                # identity claims 非 UUID/缺字段：无法映射身份 → fail-closed
                logger.warning("direct api key claims invalid, rejecting: %s", exc)
                return JSONResponse(status_code=401, content={"detail": "invalid api key"})
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "missing credentials"})

    async def _gateway_dispatch(self, request: Request, call_next):
        """通道 1 验签/ACL 委托企业处理器（判定逻辑在 enterprise_ext.memory_profile）。

        authenticate 返回 UserContext（主体，映射 Principal）；HTTPException = 拒绝
        （401/403/500，SDK 语义原样转 JSONResponse）。memory-profile 无通道 2 豁免。
        """
        try:
            ctx: UserContext | None = await self._gateway.authenticate(request)
        except HTTPException as exc:
            # 验签失败 401 / ACL 拒绝 403 / 配置缺失 500，按 SDK 语义原样返回
            logger.warning("gateway auth denied status=%s detail=%s", exc.status_code, exc.detail)
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        except Exception as exc:
            logger.warning("gateway auth failed: %s", exc)
            return JSONResponse(status_code=401, content={"detail": "invalid token"})
        if ctx is None:
            # 无通道 2 豁免：处理器不应返回 None，防御性 fail-closed
            return JSONResponse(status_code=401, content={"detail": "invalid token"})
        try:
            request.state.principal = Principal(
                actor_id=ctx.user_id,
                actor_name=None,  # claims 不携带 actor_name
                tenant_id=ctx.tenant_id,
                workspace_id=ctx.workspace_id,
            )
        except ValidationError as exc:
            # sub 非用户 UUID（如 ak:* API Key token）：无法映射身份 → fail-closed
            logger.warning("gateway principal invalid, rejecting: %s", exc)
            return JSONResponse(status_code=401, content={"detail": "invalid token"})
        return await call_next(request)

    def _kill_switch_active(self) -> bool:
        path = self._config.kill_switch_file
        if not path:
            return False
        return os.path.exists(path)


def build_memory_profile_auth_middleware(app, config: MemoryProfileAuthConfig) -> MemoryProfileAuthMiddleware:
    return MemoryProfileAuthMiddleware(app, config)


__all__ = [
    "MemoryProfileAuthConfig",
    "MemoryProfileAuthMiddleware",
    "Principal",
    "build_memory_profile_auth_middleware",
    "get_optional_principal",
    "get_principal",
    "is_public_path",
]
