"""Authentication middleware: pick the configured strategy (direct built-in /
gateway enterprise plugin) and apply its termination result.

dispatch does four things: probe whitelist → resolve the target route (aud +
forwarding target) → anonymous exemptions (KB public download, app public
share view) → run authenticate.
Credential-type routing (path prefix) and the termination judgment itself live
in src/termination.py, shared by both strategies.

Result application (both strategies terminate credentials):
- rejection (status_code set): 401 semantics / 429 rate limit (rate-limit
  headers passed through) — returned directly;
- success: request.state.internal_token (None in direct mode — no token is
  issued), sanitize client credentials + the whole identity namespace, inject
  the gateway-authenticated identity headers, re-assert the client-facing
  origin as X-Forwarded-Proto / X-Forwarded-Host, write the audit event.

KB public single-file download exemption: anonymous GET /api/files/{uuid36}
(browser <img> rendering) skips authenticate in both strategies — sanitize the
same namespace, inject the gateway-asserted X-KB-Source, then forward
(UUID-capability public read, mirroring the monolith public=True proxy
semantics; mirrors the kb whitelist in mem-knowledge src/auth.py). Requests
carrying credentials never short-circuit — they run the full pipeline
(declared identity must fail closed, never fall back to anonymous download).

App public conversation-share exemption: anonymous GET /api/apps/share/{token}
(shared-link viewer landing page) skips authenticate the same way — sanitize
the namespace and forward with no identity injected (the split serves the view
from its own public path, application-service src/auth.py). Same containment:
exact single-segment token, GET only, credentials always run the pipeline.

App public share-view API exemption: /api/public/share/* (token exchange, chat
SSE, interaction writes) skips authenticate unconditionally — every request
carries the viewer's share access token as Authorization: Bearer by contract,
which is not a platform credential and must reach the split verbatim (its
public share routes verify the share token themselves; no principal is ever
assembled). The header is captured into request.state and re-injected by the
forwarder; the scope itself stays sanitized. Identity namespace and X-Forwarded
forgeries are stripped and the origin pair re-asserted, same as terminated
paths.

X-Forwarded hardening: client-supplied X-Forwarded-* are stripped from every
path (forgery surface) and the terminated path re-injects the pair downstream
services rebuild public share URLs from — scheme + Host of the gateway's own
request view (the exact inputs the monolith used). Behind a TLS-terminating
edge, run the gateway with uvicorn proxy headers trusted (FORWARDED_ALLOW_IPS)
so request.url.scheme reflects the public scheme, same as the monolith.
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


# App public conversation-share exemption (mirrors the split public path
# /internal/v1/apps/share/{token} in application-service src/auth.py): the
# shared-link viewer holds no credentials, so anonymous GET of the share view
# is forwarded without termination. The token is the capability — exact
# single-segment match, GET only; every other /api/apps/* path stays
# authenticated.
_APP_PUBLIC_SHARE_RE = re.compile(r"/api/apps/share/[^/]+")


def is_app_public_share(path: str, method: str) -> bool:
    return method == "GET" and _APP_PUBLIC_SHARE_RE.fullmatch(path) is not None


# App public share-view API exemption (mirrors the split public path
# /internal/v1/public/share in application-service src/auth.py): the viewer
# authenticates with a share access token carried as Authorization: Bearer on
# every request, so these paths never take the credential pipeline — the token
# is verified by the split's public share routes, not by the gateway. The
# method set mirrors the split contract (GET reads, POST token exchange / chat
# / interaction writes, DELETE self-owned removals); everything else on the
# prefix stays authenticated.
_APP_PUBLIC_SHARE_API_RE = re.compile(r"^/api/public/share(?:$|/)")
_APP_PUBLIC_SHARE_API_METHODS = ("GET", "POST", "DELETE")


def is_app_public_share_api(path: str, method: str) -> bool:
    return (method in _APP_PUBLIC_SHARE_API_METHODS
            and _APP_PUBLIC_SHARE_API_RE.match(path) is not None)


def _has_credentials(request: Request) -> bool:
    """请求是否携带可验证凭据（Bearer 用户 JWT 或 API key）。"""
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") or request.headers.get("x-api-key") is not None


# Headers always removed from client requests before termination results are
# applied: credentials plus the gateway-managed identity namespace. Clients
# must never be able to forge values the gateway injects (downstream services
# trust these headers after termination). The X-Forwarded-* family is included
# — the gateway re-derives the origin pair from its own request view.
_STRIPPED_HEADERS = frozenset({
    "authorization", "x-api-key", "x-api-key-id", "x-internal-token",
})
_STRIPPED_HEADER_PREFIXES = ("x-user-", "x-tenant-", "x-workspace-", "x-kb-", "x-model-",
                             "x-app-", "x-forwarded-")


def _sanitize_headers(headers) -> dict[str, str]:
    """Strip credentials + the identity namespace; callers then inject the
    authoritative values. Lowercased per-key check: raw scope headers are
    lowercase in practice (h11/uvicorn), but never rely on that here."""
    return {k: v for k, v in headers.items()
            if k.lower() not in _STRIPPED_HEADERS
            and not k.lower().startswith(_STRIPPED_HEADER_PREFIXES)}


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
        # KB public single-file download exemption (UUID-capability public read:
        # anonymous download keyed by the file UUID, mirroring the monolith
        # public=True proxy semantics). Applies in both strategies: anonymous
        # requests (no credentials) skip authenticate and get the
        # gateway-asserted source injected; requests carrying credentials always
        # run the pipeline — a verified identity takes the kb principal path,
        # a failing one rejects 401 (declared identity never falls back to
        # anonymous download).
        if (is_kb_public_file_download(request.url.path, request.method)
                and not _has_credentials(request)):
            self._public_download_headers(request)
            return await call_next(request)
        # App public conversation-share exemption: anonymous shared-link views
        # (no credentials) are forwarded with no identity injected; requests
        # carrying credentials always run the pipeline so a failing declared
        # identity rejects 401 (never an anonymous fallback).
        if (is_app_public_share(request.url.path, request.method)
                and not _has_credentials(request)):
            self._public_share_headers(request)
            return await call_next(request)
        # App public share-view API exemption: the share access token rides
        # Authorization: Bearer by contract, so this branch is deliberately
        # unconditional — the pipeline would reject the share token as an
        # invalid user JWT. The token is captured for the forwarder and the
        # request continues without termination (the split's public share
        # routes verify it; no platform identity exists on these paths).
        if is_app_public_share_api(request.url.path, request.method):
            self._public_share_api_headers(request)
            return await call_next(request)
        result = await strategy.authenticate(request, deps)
        if result.status_code is not None:
            # Rejection: 401 semantics / 429 rate limit; rate-limit headers ride
            # the rejection response (api-key semantics preserved).
            return JSONResponse(status_code=result.status_code,
                                content={"detail": result.detail},
                                headers=result.headers or None)
        # Termination path: the identity context (UserContext/ApiKeyContext) was
        # written to request.state.identity by the pipeline (/stub echo +
        # downstream consumers); here we only apply headers/state/audit.
        request.state.internal_token = result.internal_token   # None in direct mode
        self._rewrite_headers(request, result.identity_headers)
        if deps.audit is not None and result.audit_event is not None:
            await deps.audit.audit(AuditEvent(**result.audit_event))
        return await call_next(request)

    @staticmethod
    def _rewrite_headers(request: Request, added: dict[str, str]) -> None:
        """Sanitize client credentials + the identity namespace, then merge the
        injected values and rewrite scope headers (downstream sees only the
        gateway-authenticated identity).

        The client-facing origin is re-asserted as X-Forwarded-Proto /
        X-Forwarded-Host from the gateway's own request view (scheme + Host) —
        the exact inputs the monolith computed public share URLs from; splits
        only ever see their own internal address and rebuild the origin from
        this pair. Client-supplied X-Forwarded-* were stripped above."""
        headers = _sanitize_headers(request.headers)
        headers.update(added)
        headers["x-forwarded-proto"] = request.url.scheme
        headers["x-forwarded-host"] = request.headers.get("host", "")
        request.scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]

    @staticmethod
    def _public_download_headers(request: Request) -> None:
        """Header cleanup for the anonymous public-download exemption: the same
        sanitization set as terminated requests, then the gateway-asserted
        X-KB-Source. Without it, forged channel-2 headers
        (X-KB-Actor/Tenant/Workspace/Source) would be trusted by kb as the
        monolith identity and allow cross-workspace file reads; downstream then
        only sees a trusted one-hop source marker and no forgeable identity."""
        headers = _sanitize_headers(request.headers)
        headers["x-kb-source"] = _KB_PUBLIC_DOWNLOAD_SOURCE
        request.scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]

    @staticmethod
    def _public_share_headers(request: Request) -> None:
        """Header cleanup for the anonymous conversation-share exemption: the
        same sanitization set as terminated requests and no identity injected —
        the viewer has no identity to assert and the split serves the view from
        its public path. Forged X-App-* / X-User-* headers must not survive
        even so: the namespace is gateway-managed on every other path."""
        headers = _sanitize_headers(request.headers)
        request.scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]

    @staticmethod
    def _public_share_api_headers(request: Request) -> None:
        """Header cleanup for the public share-view API exemption: sanitize the
        identity namespace (forged X-App-* / X-User-* must not survive), capture
        the client's share access token for the forwarder (it never rides the
        scope — same mechanics as the internal token), and re-assert the origin
        pair the split rebuilds public share URLs from."""
        request.state.forward_authorization = request.headers.get("authorization")
        headers = _sanitize_headers(request.headers)
        headers["x-forwarded-proto"] = request.url.scheme
        headers["x-forwarded-host"] = request.headers.get("host", "")
        request.scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
