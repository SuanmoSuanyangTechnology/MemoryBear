"""认证中间件：按配置选鉴权策略（direct 内置 / gateway 企业插件）并应用结果。

dispatch 只做三件事：白名单放行 → 解析目标路由（aud 动态化）→ 选定策略执行
authenticate。凭据类型判定（路径前缀）与验签/快照/黑名单/限流/签发内部 token
的全部逻辑已迁至 src/auth_strategy.py（GatewayAuthStrategy/DirectAuthStrategy）。

应用结果三态：
- 拒绝（status_code 非 None）：401 各语义 / 429 限流（附带限流头），直接回包；
- direct 透传：凭据原样保留给下游服务自验（SDK 入站），网关只做按 IP 限流；
- gateway 终结：剥除原始凭据 → 注入身份头/内部 token（claims 权威，§2.4）→ 审计。
"""
import logging
from dataclasses import dataclass

from auth_sdk.audit import AuditLogger
from auth_sdk.ratelimit import ApiKeyRateLimiter
from auth_sdk.schema import AuditEvent
from auth_sdk.snapshot import SnapshotReader
from auth_sdk.token import LocalTokenIssuer, TokenVerifier
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .auth_strategy import AuthStrategy, load_strategy
from .config import settings
from .forward import TargetResolver

logger = logging.getLogger(__name__)


@dataclass
class GatewayDeps:
    verifier: TokenVerifier
    reader: SnapshotReader
    issuer: LocalTokenIssuer
    limiter: ApiKeyRateLimiter
    audit: AuditLogger | None
    resolver: TargetResolver   # 路径 → 目标路由（aud 动态化 + 转发目标）


class GatewayMiddleware(BaseHTTPMiddleware):
    """从 app.state.gateway_deps 动态取依赖——e2e 启动后可注入，避免构造时 None 固化。

    strategy 可显式注入（测试/接线直选），缺省按 settings.auth_strategy_name
    （AUTH_STRATEGY env，惰性读）经 load_strategy 加载。
    """

    def __init__(self, app, deps_getter, strategy: AuthStrategy | None = None):
        super().__init__(app)
        self._deps_getter = deps_getter
        self._strategy = strategy

    async def dispatch(self, request, call_next):
        # K8s probe 白名单：不依赖 Redis/DB，探活期间外部依赖故障不影响 liveness 判定
        if request.url.path == "/healthz":
            return await call_next(request)
        deps = self._deps_getter()               # 局部变量，避免并发 await 期间实例属性互覆
        # 先解析目标路由（aud 动态化 + 转发层取 route）；未命中即 None，走 stub 语义
        request.state.target_route = deps.resolver.resolve(request.url.path)
        strategy = self._strategy or load_strategy(settings.auth_strategy_name, deps)
        result = await strategy.authenticate(request, deps)
        if result.status_code is not None:
            # 拒绝：401 各语义 / 429 限流；限流头随拒绝回包（gateway api-key 语义保持）
            return JSONResponse(status_code=result.status_code,
                                content={"detail": result.detail},
                                headers=result.headers or None)
        if result.direct:
            return await call_next(request)          # 透传：凭据原样给下游自验
        # gateway 终结路径：身份上下文（UserContext/ApiKeyContext）已由策略写入
        # request.state.identity（/stub 回显 + 下游服务取主体），此处只做头/状态/审计
        request.state.internal_token = result.internal_token
        self._rewrite_headers(request, result.identity_headers)
        if deps.audit is not None and result.audit_event is not None:
            await deps.audit.audit(AuditEvent(**result.audit_event))
        return await call_next(request)

    @staticmethod
    def _rewrite_headers(request: Request, added: dict[str, str]) -> None:
        """剥除原始凭据头 + 合并注入头后重写 scope headers（下游只见注入后的头）。"""
        headers = dict(request.headers)
        headers.pop("authorization", None)   # 剥除用户/API key 凭据，下游见不到
        headers.pop("x-api-key", None)
        headers.update(added)
        request.scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
