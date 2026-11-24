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

KB 公开例外：匿名 GET /api/files/{uuid36}（浏览器 <img> 渲染）以及 /v1 面
（GET /v1/files/{uuid36} 与四个静态枚举）在两种策略下都跳过 authenticate——清洗
同一命名空间，注入网关断言的 X-KB-Source（/api 注 in_api、/v1 注 ex_api）后转发
（UUID 能力公开读，镜像单体 public=True 代理语义；镜像 mem-knowledge src/auth.py
的 kb 白名单）。带凭据的请求绝不短路——一律走完整管线（已声明身份必须 fail-closed，
绝不回落匿名下载）。

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
import time
import uuid
from dataclasses import dataclass

from auth_sdk.audit import AuditLogger
from auth_sdk.ratelimit import ApiKeyRateLimiter
from auth_sdk.schema import ApiKeyContext, AuditEvent
from auth_sdk.snapshot import SnapshotReader
from auth_sdk.token import LocalTokenIssuer, TokenVerifier
from fastapi import Request
from memorybear_grpc.metadata import traceparent_trace_id
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .anonymous import (
    is_license_status,
    is_storage_public_read,
    is_storage_share_upload,
    is_workflow_webhook,
)
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


# KB 公开例外的 /v1 面（镜像单体 rag_api_* public=True 与 kb auth 白名单）：匿名
# 单文件下载 + 四个静态枚举。与 /api 面同样精确匹配、仅 GET、带凭据不回落匿名。
# 注入面标识 ex_api——kb get_public_file 接受 EXTERNAL_API/MANAGER_API，枚举端点
# 不消费来源。不扩大不收紧：仅这五个 /v1 端点公开。
_KB_PUBLIC_V1_FILE_DOWNLOAD_RE = re.compile(r"/v1/files/[0-9a-fA-F-]{36}")
_KB_PUBLIC_V1_ENUM_PATHS = frozenset({
    "/v1/knowledges/knowledgetype",
    "/v1/knowledges/permissiontype",
    "/v1/knowledges/parsertype",
    "/v1/chunks/retrieve_type",
})
_KB_PUBLIC_V1_SOURCE = "ex_api"


def is_kb_public_v1_endpoint(path: str, method: str) -> bool:
    if method != "GET":
        return False
    return (_KB_PUBLIC_V1_FILE_DOWNLOAD_RE.fullmatch(path) is not None
            or path in _KB_PUBLIC_V1_ENUM_PATHS)


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


# App citation-download exemption (mirrors the split public path
# /internal/v1/apps/citations/{uuid}/download in application-service
# src/auth.py and the monolith route, which performs no permission check — the
# document UUID is the capability). The console opens the URL in a new tab, so
# no Authorization header can ride along. Exact UUID, GET only; every other
# /api/apps/* path stays authenticated.
_APP_PUBLIC_CITATION_DOWNLOAD_RE = re.compile(
    r"/api/apps/citations/[0-9a-fA-F-]{36}/download"
)


def is_app_public_citation_download(path: str, method: str) -> bool:
    return (method == "GET"
            and _APP_PUBLIC_CITATION_DOWNLOAD_RE.fullmatch(path) is not None)


def _has_credentials(request: Request) -> bool:
    """请求是否携带可验证凭据（Bearer 用户 JWT 或 API key）。"""
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") or request.headers.get("x-api-key") is not None


# Headers always removed from client requests before termination results are
# applied: credentials plus the gateway-managed identity namespace. Clients
# must never be able to forge values the gateway injects (downstream services
# trust these headers after termination). The X-Forwarded-* family is included
# — the gateway re-derives the origin pair from its own request view.
_STRIPPED_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "x-api-key-id",
        "x-internal-token",
        "x-source",
    }
)
_STRIPPED_HEADER_PREFIXES = (
    "x-user-",
    "x-tenant-",
    "x-workspace-",
    "x-kb-",
    "x-model-",
    "x-app-",
    "x-operator-",
    "x-forwarded-"
)


def _sanitize_headers(headers) -> dict[str, str]:
    """Strip credentials + the identity namespace; callers then inject the
    authoritative values. Lowercased per-key check: raw scope headers are
    lowercase in practice (h11/uvicorn), but never rely on that here."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _STRIPPED_HEADERS
        and not k.lower().startswith(_STRIPPED_HEADER_PREFIXES)
    }


_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _resolve_trace_id(request: Request) -> str:
    """Client trace id (or a valid W3C traceparent) kept for correlation; a
    generated id otherwise. Locally produced rejections never reach a
    downstream service, so the gateway must supply the trace itself."""
    trace_id = (
        request.headers.get("x-trace-id")
        or traceparent_trace_id(request.headers.get("traceparent"))
    )
    if trace_id and _TRACE_ID_PATTERN.fullmatch(trace_id):
        return trace_id
    return uuid.uuid4().hex


@dataclass
class GatewayDeps:
    verifier: TokenVerifier
    reader: SnapshotReader
    issuer: LocalTokenIssuer
    limiter: ApiKeyRateLimiter
    audit: AuditLogger | None
    resolver: TargetResolver  # 路径 → 目标路由（aud 动态化 + 转发目标）


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
        # Probe and scrape paths: no credentials and no Redis/DB dependency.
        # Liveness and metrics collection must survive auth-infrastructure
        # outages; /metrics is internal-only by ingress routing, not by auth.
        if request.url.path in ("/healthz", "/metrics"):
            return await call_next(request)
        deps = self._deps_getter()  # 局部变量，避免并发 await 期间实例属性互覆
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
        if is_kb_public_file_download(
            request.url.path, request.method
        ) and not _has_credentials(request):
            self._public_download_headers(request)
            return await call_next(request)
        # KB /v1 公开例外（匿名单文件下载 + 静态枚举，镜像单体 rag_api_* public=True
        # 契约）。与 /api 下载同样的约束：精确匹配、仅 GET、带凭据一律走管线。注入
        # 外部面标识，使 kb 按 UUID 能力公开读。
        if is_kb_public_v1_endpoint(
            request.url.path, request.method
        ) and not _has_credentials(request):
            self._public_download_headers(request, _KB_PUBLIC_V1_SOURCE)
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
        # App anonymous citation-download exemption: the browser opens the URL
        # in a new tab with no credentials. Requests carrying credentials always
        # run the pipeline, so a failing declared identity rejects 401 instead
        # of falling back to anonymous.
        if (is_app_public_citation_download(request.url.path, request.method)
                and not _has_credentials(request)):
            self._public_citation_download_headers(request)
            return await call_next(request)
        # Workflow webhook entry: external systems post to the route key with
        # no platform credential; the host route had no auth dependency either.
        # Unconditional by contract -- a stray credential is ignored, and every
        # identity header is stripped before forwarding.
        if is_workflow_webhook(request.url.path, request.method):
            self._anonymous_headers(request)
            return await call_next(request)
        # License status read: the console polls it even while other paths are
        # rejected by the license middleware, so the path is whitelisted there
        # and anonymous here (monolith parity: no auth dependency on the
        # route). Unconditional; identity namespace stripped.
        if is_license_status(request.url.path, request.method):
            self._anonymous_headers(request)
            return await call_next(request)
        # File-service public reads (permanent/public/signed URL/status/info):
        # the browser carries no platform credential; requests carrying one run
        # the full pipeline (declared identity fails closed, never falls back).
        if (is_storage_public_read(request.url.path, request.method)
                and not _has_credentials(request)):
            self._anonymous_headers(request)
            return await call_next(request)
        # Share-token upload: Authorization carries the viewer's share access
        # token by contract; captured for the forwarder, same as /api/public/share.
        if is_storage_share_upload(request.url.path, request.method):
            self._public_share_api_headers(request)
            return await call_next(request)
        # Platform-admin passthrough (/sys): the gateway neither verifies nor
        # terminates the credential - the platform-admin JWT rides Authorization
        # verbatim to the target service, which verifies it itself. The origin
        # pair is re-asserted and the forgeable identity namespace is stripped,
        # exactly like the public share-view exemption.
        route = request.state.target_route
        if route is not None and route.auth_mode == "passthrough":
            self._passthrough_headers(request)
            return await call_next(request)
        result = await strategy.authenticate(request, deps)
        if result.status_code is not None:
            # Rejection: 401 semantics / 429 rate limit; rate-limit headers ride
            # the rejection response (api-key semantics preserved). The trace id
            # is echoed/generated here because these rejections never reach a
            # downstream service that would add it.
            rejection_headers = dict(result.headers or {})
            rejection_headers["X-Trace-Id"] = _resolve_trace_id(request)
            return JSONResponse(
                status_code=result.status_code,
                # 所有凭据拒绝统一 fail 信封：/v1 契约 3007/3009，
                # /api 用户 JWT 拒绝 2001/2002/2004/4009
                content=result.body if result.body is not None
                else {"detail": result.detail},
                headers=rejection_headers,
            )
        # Termination path: the identity context (UserContext/ApiKeyContext) was
        # written to request.state.identity by the pipeline (/stub echo +
        # downstream consumers); here we only apply headers/state/audit.
        request.state.internal_token = result.internal_token  # None in direct mode
        self._rewrite_headers(request, result.identity_headers)
        if deps.audit is not None and result.audit_event is not None:
            await deps.audit.audit(AuditEvent(**result.audit_event))
        identity = request.state.identity
        if not isinstance(identity, ApiKeyContext) or deps.audit is None:
            return await call_next(request)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            # Persist separately from auth.granted: each accepted API-key request
            # produces exactly one usage row, including downstream failures.
            await deps.audit.audit(AuditEvent(
                event_type="api_key.request",
                actor_id=identity.user_id,
                tenant_id=identity.tenant_id,
                target=f"{request.method} {request.url.path}",
                result="ok" if status_code < 400 else "error",
                detail={
                    "api_key_id": identity.api_key_id,
                    "endpoint": request.url.path,
                    "method": request.method,
                    "status_code": status_code,
                    "response_time": round((time.perf_counter() - started) * 1000),
                },
            ))

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
    def _public_download_headers(
        request: Request, source: str = _KB_PUBLIC_DOWNLOAD_SOURCE
    ) -> None:
        """匿名 KB 公开例外的请求头清洗：与终止请求相同的清洗集，然后注入网关断言的
        X-KB-Source（/api 下载注 in_api、/v1 面注 ex_api）。否则伪造的通道 2 头
        （X-KB-Actor/Tenant/Workspace/Source）会被 kb 当作单体身份信任，导致跨工作区
        读文件；清洗后下游只看到可信的一跳来源标记，无可伪造身份。"""
        headers = _sanitize_headers(request.headers)
        headers["x-kb-source"] = source
        request.scope["headers"] = [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ]

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
    def _public_citation_download_headers(request: Request) -> None:
        """Header cleanup for the anonymous citation-download exemption: the
        same sanitization set as terminated requests and no identity injected —
        the endpoint has no permission check (the document UUID is the
        capability). Forged X-App-* / X-User-* headers must not survive."""
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

    @staticmethod
    def _passthrough_headers(request: Request) -> None:
        """Header cleanup for auth_mode=passthrough routes: capture the platform
        admin credential for the forwarder (never rides the scope), strip the
        gateway-managed identity namespace so forged X-User-* / X-Tenant-* /
        X-Platform-* headers cannot reach the target, and re-assert the origin
        pair from the gateway's own request view."""
        request.state.forward_authorization = request.headers.get("authorization")
        headers = _sanitize_headers(request.headers)
        headers["x-forwarded-proto"] = request.url.scheme
        headers["x-forwarded-host"] = request.headers.get("host", "")
        request.scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in headers.items()]

    @staticmethod
    def _anonymous_headers(request: Request) -> None:
        """Sanitize the identity namespace, inject no identity, and re-assert the
        origin pair — file-service rebuilds remote-storage presigned URLs from
        X-Forwarded-Proto, otherwise https pages get http redirects."""
        headers = _sanitize_headers(request.headers)
        headers["x-forwarded-proto"] = request.url.scheme
        headers["x-forwarded-host"] = request.headers.get("host", "")
        request.scope["headers"] = [
            (k.lower().encode(), v.encode()) for k, v in headers.items()]
