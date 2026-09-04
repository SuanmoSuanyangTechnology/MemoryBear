"""网关服务入口。

首期职责：验签用户 JWT（HS256 + type=access）→ 读 Redis 用户快照（fail-closed，
决策 #14）→ 剥除用户凭据 → 本地签发内部 token（RS256，TTL 120s）→ 注入身份头
转发。deps 经 app.state 动态注入，便于测试替换（ASGITransport 不触发 lifespan）。
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
