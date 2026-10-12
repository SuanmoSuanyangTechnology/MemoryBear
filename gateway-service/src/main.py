"""网关服务入口。

Termination + forwarding pipeline: user JWT verification (HS256 + type=access)
→ Redis user snapshot (fail-closed) → credential stripping → identity-header
injection. Enterprise gateway mode additionally signs an internal token (RS256,
TTL 120s); direct mode does not. deps are injected via app.state so tests can
replace them (ASGITransport does not trigger lifespan).
"""
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src import config, redis as gredis
from src.forward import Forwarder, StaticTargetResolver
from src.middleware import GatewayDeps, GatewayMiddleware
from src.routes import router
from auth_sdk.token import TokenVerifier, LocalTokenIssuer
from auth_sdk.snapshot import SnapshotReader
from auth_sdk.ratelimit import ApiKeyRateLimiter
from auth_sdk.audit import AuditLogger


def build_gateway_deps() -> GatewayDeps:
    # 目标路由 + 转发器只建一次（lifespan 内调用，此时模块级 app 已就绪）
    resolver = StaticTargetResolver(routes=config.settings.target_routes)
    forwarder = Forwarder(client=httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)))
    app.state.forwarder = forwarder
    return GatewayDeps(
        verifier=TokenVerifier(secret=config.settings.SECRET_KEY),
        # backfill=None：快照 miss 直接 401，不穿透回源 DB（决策 #14 fail-closed）；
        # 回源能力由未来版本接 identity 的 /internal/user-snapshot 接口补齐
        reader=SnapshotReader(gredis.redis, backfill=None,
                              timeout_ms=config.settings.REDIS_CMD_TIMEOUT_MS),
        issuer=LocalTokenIssuer(private_key=config.settings.INTERNAL_ISSUER_PRIVATE_KEY,
                                kid=config.settings.INTERNAL_ISSUER_KID,
                                ttl=config.settings.INTERNAL_TOKEN_TTL,
                                leeway=config.settings.INTERNAL_TOKEN_LEEWAY),
        limiter=ApiKeyRateLimiter(gredis.redis),
        audit=AuditLogger(gredis.redis, stream_key=config.settings.AUDIT_STREAM_KEY,
                          timeout_ms=config.settings.REDIS_CMD_TIMEOUT_MS),
        resolver=resolver)


def get_deps():
    return app.state.gateway_deps


@asynccontextmanager
async def lifespan(app: FastAPI):
    await gredis.init_redis()
    app.state.gateway_deps = build_gateway_deps()
    yield
    await gredis.close_redis()

app = FastAPI(title="gateway-service", lifespan=lifespan)
app.include_router(router)
app.add_middleware(GatewayMiddleware, deps_getter=get_deps)
