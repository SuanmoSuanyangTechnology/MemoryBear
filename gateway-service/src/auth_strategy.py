"""Auth strategy abstraction: direct built-in (community) / gateway enterprise plugin.

Both strategies terminate credentials at the gateway and inject identity
headers; they differ only in the exit:
- direct: run the shared termination pipeline (src/termination.py) — verify,
  snapshot, blacklist, rate limit — and inject identity headers, no internal token;
- gateway: the same pipeline plus internal-token issuance. Not in this repo:
  implemented by the private enterprise-extensions package (lazily loaded when
  AUTH_STRATEGY=gateway; absence is a loud RuntimeError because an open-source
  build cannot install that package).

The protocol contract (AuthStrategy/AuthResult) is the only interface between
the enterprise strategy and the open-source side; adding a new strategy domain
only adds a name branch + private package module, never a middleware rework.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from fastapi import Request

if TYPE_CHECKING:
    # 仅注解用：middleware.py 模块级 import 本模块，运行时反引会成环（部分初始化失败）
    from .middleware import GatewayDeps


@dataclass
class AuthResult:
    internal_token: str | None = None
    identity_headers: dict[str, str] = field(default_factory=dict)
    status_code: int | None = None          # 非 None = 拒绝响应
    detail: str | None = None
    audit_event: dict | None = None         # {event_type, actor_id, tenant_id, target, result}
    headers: dict[str, str] = field(default_factory=dict)  # 拒绝响应附加头（429 限流头）


class AuthStrategy(Protocol):
    async def authenticate(self, request: Request, deps: GatewayDeps) -> AuthResult: ...


class DirectAuthStrategy:
    """Community shape: terminate credentials via the shared pipeline and inject
    identity headers only (no internal token; downstream trusts the headers)."""

    async def authenticate(self, request: Request, deps: GatewayDeps) -> AuthResult:
        # Deferred import: termination.py imports AuthResult from this module at
        # module level — a top-level import here would close the cycle.
        from .termination import terminate
        return await terminate(request, deps)


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
