"""网关服务入口。

Termination + forwarding pipeline: user JWT verification (HS256 + type=access)
→ Redis user snapshot (fail-closed) → credential stripping → identity-header
injection. Enterprise gateway mode additionally signs an internal token (RS256,
TTL 120s); direct mode does not. deps are injected via app.state so tests can
replace them (ASGITransport does not trigger lifespan).
"""
from contextlib import asynccontextmanager

import httpx
from auth_sdk.audit import AuditLogger
from auth_sdk.ratelimit import ApiKeyRateLimiter
from auth_sdk.snapshot import SnapshotReader
from auth_sdk.token import LocalTokenIssuer, TokenVerifier
from fastapi import FastAPI
from memorybear_grpc.client import ChannelPool

from src import config
from src import redis as gredis
from src.forward import Forwarder, StaticTargetResolver
from src.identity_backfill import IdentityApiKeyBackfill
from src.grpc_forward import GrpcForwarder
from src.middleware import GatewayDeps, GatewayMiddleware
from src.routes import router
from src.stream_gate import StreamGate


def build_gateway_deps() -> GatewayDeps:
    # Target routes and forwarders are built once per process. HTTP and gRPC
    # share the stream gate and the application-service circuit breaker: one
    # concurrency budget and one health signal cover the same upstream service.
    resolver = StaticTargetResolver(routes=config.settings.target_routes)
    timeout = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
    gate = StreamGate(config.settings.gateway_stream_max_connections)
    forwarder = Forwarder(
        client=httpx.AsyncClient(timeout=timeout),
        sse_idle_timeout=config.settings.gateway_stream_idle_timeout_s,
        gate=gate,
    )
    pool = ChannelPool(
        max_message_bytes=config.settings.grpc_max_message_bytes,
        keepalive_time_ms=config.settings.grpc_keepalive_time_ms,
        keepalive_timeout_ms=config.settings.grpc_keepalive_timeout_ms,
    )
    app.state.forwarder = forwarder
    # Keys issued before the snapshot pipeline have no Redis entry; lazily
    # rebuild them from identity. Without a verify URL, a miss stays a 401.
    backfill = None
    if config.settings.IDENTITY_API_KEY_VERIFY_URL:
        backfill = IdentityApiKeyBackfill(
            config.settings.IDENTITY_API_KEY_VERIFY_URL,
            timeout_ms=config.settings.IDENTITY_API_KEY_VERIFY_TIMEOUT_MS)
    app.state.identity_api_key_backfill = backfill
    app.state.grpc_pool = pool
    app.state.grpc_forwarder = GrpcForwarder(
        pool=pool,
        gate=gate,
        circuit=forwarder.circuit_for("application-service"),
        protocol_override=config.settings.app_chat_protocol,
        default_target=config.settings.application_service_grpc_target,
        deadline_s=config.settings.gateway_grpc_deadline_s,
        heartbeat_s=config.settings.gateway_stream_heartbeat_s,
        idle_timeout_s=config.settings.gateway_stream_idle_timeout_s,
    )
    return GatewayDeps(
        verifier=TokenVerifier(secret=config.settings.SECRET_KEY),
        # backfill=None：用户快照 miss 直接 401，不穿透回源 DB（决策 #14 fail-closed）；
        # 回源能力由未来版本接 identity 的 /internal/user-snapshot 接口补齐
        reader=SnapshotReader(gredis.redis, backfill=None,
                              api_key_backfill=backfill,
                              api_key_miss_ttl=config.settings.API_KEY_MISS_CACHE_TTL_SECONDS,
                              timeout_ms=config.settings.REDIS_CMD_TIMEOUT_MS),
        issuer=LocalTokenIssuer(private_key=config.settings.INTERNAL_ISSUER_PRIVATE_KEY,
                                kid=config.settings.INTERNAL_ISSUER_KID,
                                ttl=config.settings.INTERNAL_TOKEN_TTL,
                                leeway=config.settings.INTERNAL_TOKEN_LEEWAY),
        limiter=ApiKeyRateLimiter(gredis.redis),
        audit=AuditLogger(gredis.redis, stream_key=config.settings.audit_stream_key_name,
                          timeout_ms=config.settings.REDIS_CMD_TIMEOUT_MS,
                          maxlen=config.settings.AUDIT_STREAM_MAXLEN),
        resolver=resolver)


def get_deps():
    return app.state.gateway_deps


@asynccontextmanager
async def lifespan(app: FastAPI):
    await gredis.init_redis()
    try:
        app.state.gateway_deps = build_gateway_deps()
        yield
    finally:
        backfill = getattr(app.state, "identity_api_key_backfill", None)
        if backfill is not None:
            await backfill.aclose()
        pool = getattr(app.state, "grpc_pool", None)
        if pool is not None:
            await pool.aclose(grace_s=config.settings.grpc_shutdown_grace_s)
        await gredis.close_redis()

app = FastAPI(title="gateway-service", lifespan=lifespan)
app.include_router(router)
app.add_middleware(GatewayMiddleware, deps_getter=get_deps)
