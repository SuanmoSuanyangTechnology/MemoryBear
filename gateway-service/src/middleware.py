"""认证中间件：按配置选鉴权策略（direct 内置 / gateway 企业插件）并应用结果。

dispatch 只做四件事：白名单放行 → 解析目标路由（aud 动态化）→ 公开下载例外判定
（gateway 模式）→ 选定策略执行 authenticate。凭据类型判定（路径前缀）与验签/
快照/黑名单/限流/签发内部 token 的全部逻辑已迁至 src/auth_strategy.py
（GatewayAuthStrategy/DirectAuthStrategy）。

应用结果三态：
- 拒绝（status_code 非 None）：401 各语义 / 429 限流（附带限流头），直接回包；
- direct 透传：凭据原样保留给下游服务自验（SDK 入站），网关只做按 IP 限流；
- gateway 终结：剥除原始凭据 → 注入身份头/内部 token（claims 权威，§2.4）→ 审计。

KB 公开单文件下载例外（gateway 终结模式）：GET /api/files/{uuid36} 无凭据请求
（浏览器 <img> 渲染）跳过 authenticate——剥净客户端凭据与全部 X-KB-* 头后注入
网关断言的 X-KB-Source 转发（UUID 能力式公开，对齐老单体 public=True 代理语义；
镜像 kb 白名单，见 mem-knowledge src/auth.py）。带凭据请求不短路，照常验签。
"""
import logging
import re
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

# KB 公开单文件下载例外（镜像 kb 白名单 /internal/v1/files/{uuid36}）：gateway 侧
# 匹配外部路径 /api/files/{uuid36}（Forwarder.internal_path 映射到 kb 内部路径）。
# 必须精确匹配整段路径：文件列表等其余 /api/files/* 若公开，攻击者可伪造通道 2
# 身份头跨工作区拉取文件。公开仅限 GET。
_KB_PUBLIC_FILE_DOWNLOAD_RE = re.compile(r"/api/files/[0-9a-fA-F-]{36}")
# 公开放行注入的 X-KB-Source 取值 = kb KnowledgeRetrievalSource.MANAGER_API.value
# （"in_api"）：对齐老单体 route_through_knowledge_service(source=MANAGER_API,
# public=True) 的请求头语义——kb 路由层 principal=None + source=in_api 时走
# get_public_file（按 UUID 公开下载），否则 400 KB_PRINCIPAL_INVALID。
_KB_PUBLIC_DOWNLOAD_SOURCE = "in_api"


def is_kb_public_file_download(path: str, method: str) -> bool:
    return method == "GET" and _KB_PUBLIC_FILE_DOWNLOAD_RE.fullmatch(path) is not None


def _has_credentials(request: Request) -> bool:
    """请求是否携带可验证凭据（Bearer 用户 JWT 或 API key）。"""
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") or request.headers.get("x-api-key") is not None


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
        # KB 公开单文件下载例外（UUID 能力式公开：凭 URL 中文件 UUID 匿名下载，对齐
        # 老单体 public=True 语义）。首期仅 gateway 终结模式启用——direct 透传模式
        # 凭据原样交下游自验、无凭据请求本就透传，网关断言 source 无定义（行为不变）。
        # 带凭据请求不短路：照常走 authenticate——验签通过即 kb 通道 1 principal 语义，
        # 失败 401 fail-closed（对齐 kb 中间件"声明了身份不得回退公开下载"）。
        if (settings.auth_strategy_name == "gateway"
                and is_kb_public_file_download(request.url.path, request.method)
                and not _has_credentials(request)):
            self._public_download_headers(request)
            return await call_next(request)
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

    @staticmethod
    def _public_download_headers(request: Request) -> None:
        """公开下载放行前的头清洗：剥净客户端凭据头与全部 X-KB-*。

        公开例外走无内部 token 的转发分支（build_headers 对客户端 X-KB-* 原样透传）
        ——若不过滤，伪造的通道 2 头（X-KB-Actor-ID/Tenant/Workspace/Source）会被 kb
        当作受信老单体身份跨工作区拉取文件。随后注入网关断言的 X-KB-Source（可信
        一跳声明，等价老单体代理），下游只见 source 标记、无任何可伪造身份面。
        """
        headers = dict(request.headers)
        for name in list(headers):
            low = name.lower()
            if low in ("authorization", "x-api-key", "x-internal-token") \
                    or low.startswith("x-kb-"):
                del headers[name]
        headers["x-kb-source"] = _KB_PUBLIC_DOWNLOAD_SOURCE
        request.scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
