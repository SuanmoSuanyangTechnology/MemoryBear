"""鉴权策略抽象（评审稿 4.2.1）：direct 内置（社区版）/ gateway 企业扩展。

direct：网关不终结凭据——透传 Authorization/X-API-Key 给下游服务自验（SDK 入站），
网关只做 Redis 限流（按 IP 计数，评审焦点 #12）。
gateway：终结凭据 → 验签/快照/黑名单 → 签发内部 token + 注入身份头。
企业版 gateway 策略不在本仓库：实现位于私有 enterprise-extensions 包
（AUTH_STRATEGY=gateway 时经 load_strategy 惰性加载，缺失即配置错误响亮暴露），
遵循企业/开源拆分规范（docs 见外层仓库）。

协议契约（AuthStrategy/AuthResult）是企业策略与开源侧的唯一接口，须保持稳定；
新增企业策略域时只加 name 分支 + 私有包模块，不改 middleware 骨架。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from fastapi import Request

if TYPE_CHECKING:
    # 仅注解用：middleware.py 模块级 import 本模块，运行时反引会成环（部分初始化失败）
    from .middleware import GatewayDeps

logger = logging.getLogger(__name__)


@dataclass
class AuthResult:
    direct: bool = False
    internal_token: str | None = None
    identity_headers: dict[str, str] = field(default_factory=dict)
    status_code: int | None = None          # 非 None = 拒绝响应
    detail: str | None = None
    audit_event: dict | None = None         # {event_type, actor_id, tenant_id, target, result}
    headers: dict[str, str] = field(default_factory=dict)  # 拒绝响应附加头（429 限流头）


class AuthStrategy(Protocol):
    async def authenticate(self, request: Request, deps: GatewayDeps) -> AuthResult: ...


class DirectAuthStrategy:
    """社区版：透传凭据 + 按 IP 限流。不读快照/黑名单，不签发内部 token。"""

    async def authenticate(self, request: Request, deps: GatewayDeps) -> AuthResult:
        ip = request.client.host if request.client else "unknown"
        # 固定窗口按 IP 限流（fail-open：Redis 故障旁路，评审稿 4.2.2）
        try:
            allowed, headers = await deps.limiter.check_qps(f"ip:{ip}", 100)
            if not allowed:
                # 限流头随 429 回包（middleware 透出，与 gateway 语义一致）
                return AuthResult(status_code=429, detail="rate limit exceeded",
                                  headers=headers)
        except Exception:
            logger.warning("rate limit bypassed (redis down): ip=%s", ip)
        return AuthResult(direct=True)


def load_strategy(name: str, deps: GatewayDeps) -> AuthStrategy:
    """按配置加载策略。

    direct：内置社区实现；gateway：私有 enterprise-extensions 包惰性加载
    （企业扩展缺失即 RuntimeError——misconfiguration 响亮暴露，不静默降级，
    与 fail-closed 语义一致；开源构建装不到该包，AUTH_STRATEGY=gateway 属配置错误）。
    """
    if name == "direct":
        return DirectAuthStrategy()
    if name == "gateway":
        try:
            from enterprise_ext.gateway import GatewayAuthStrategy
        except ImportError as exc:
            raise RuntimeError(
                "AUTH_STRATEGY=gateway requires the private 'enterprise-extensions' "
                "package (not installed in open-source builds)") from exc
        return GatewayAuthStrategy()
    raise ValueError(f"unknown auth strategy: {name}")
