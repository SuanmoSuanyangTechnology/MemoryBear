"""Shared credential-termination pipeline for the direct and gateway strategies.

Both deployment shapes terminate credentials at the gateway and differ only in
the exit: the community direct strategy injects identity headers; the enterprise
gateway strategy (enterprise-extensions) additionally issues an internal token.
Keeping the judgment here, once, prevents the two shapes from drifting:

    verify signature → logout blacklist → identity snapshot (fail-closed)
    → disabled/tenant/password-reset checks → per-user rate limit
    → request.state.identity → inject identity headers (generic trio + service
      profile).

Requests under API_KEY_PATH_PREFIXES take the API-key variant (snapshot +
QPS/daily quota). Failure texts match the enterprise flow this was migrated
from, so both shapes reject identically.
"""
from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING

from auth_sdk.schema import UserContext
from auth_sdk.snapshot import SnapshotUnavailable
from fastapi import Request
from jose.exceptions import JWTError

from . import redis as gredis
from .auth_strategy import AuthResult
from .config import settings
from .ratelimit import FixedWindowRateLimiter

if TYPE_CHECKING:
    # Annotation use only: middleware.py imports auth_strategy at module level,
    # a runtime import here would close the cycle.
    from .middleware import GatewayDeps

logger = logging.getLogger(__name__)

# Per-service identity-header profile layered on top of the generic trio: the
# names match what each downstream service already reads (no new generic
# headers). Unknown services / unrouted paths (stub) get the trio only.
_PROFILE_HEADERS: dict[str, tuple[str, str, str]] = {
    "kb": ("X-KB-Actor-ID", "X-KB-Tenant-ID", "X-KB-Workspace-ID"),
    "model-service": ("X-Model-Actor-ID", "X-Model-Tenant-ID", "X-Model-Workspace-ID"),
}


def _extract_api_key(request: Request) -> str | None:
    """API key extraction: Authorization: Bearer <key> first, then X-API-Key
    (mirrors the monolith extract_api_key_from_request semantics — under the
    /v1/ prefix a Bearer value is an API key, not a user JWT)."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        key = auth.removeprefix("Bearer ").strip()
        if key:
            return key
    return request.headers.get("x-api-key") or None


def _profile_headers(request: Request, actor_id: str, tenant_id: str,
                     workspace_id: str) -> dict[str, str]:
    """Service-specific identity headers for the resolved target route
    (no route / unknown service → none, e.g. stub semantics)."""
    route = getattr(request.state, "target_route", None)
    names = _PROFILE_HEADERS.get(route.service) if route is not None else None
    if names is None:
        return {}
    return dict(zip(names, (actor_id, tenant_id, workspace_id)))


async def terminate_user(request: Request, deps: GatewayDeps) -> AuthResult:
    """User JWT path (/api/, /stub, ...): verify/blacklist/snapshot → per-user
    rate limit → identity context + injected headers + audit event.

    Failures return an AuthResult with detail (middleware converts to 401);
    success writes request.state.identity for stub echo / downstream consumers.
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return AuthResult(status_code=401, detail="missing token")
    token = auth.removeprefix("Bearer ").strip()
    try:
        payload = await deps.verifier.verify_jwt(token, token_type="access")
        # Token-level logout invalidation: logout / single-session kick /
        # refresh-replace write token_blacklist:{jti} on the monolith side.
        jti = payload.get("jti")
        if jti is not None and await deps.reader.is_token_blacklisted(jti):
            return AuthResult(status_code=401, detail="token blacklisted")
        snap = await deps.reader.get_user_snapshot(payload["sub"])
    except SnapshotUnavailable:
        # Snapshot/blacklist state unobtainable → reject (fail-closed); no DB
        # backfill on miss, recovery rides the invalidation-notify rebuild path.
        return AuthResult(status_code=401, detail="auth unavailable")
    except (JWTError, ValueError) as exc:  # every verify_jwt failure: signature/aud/type/missing key
        logger.warning("token verification failed: %s", exc)
        return AuthResult(status_code=401, detail="invalid token")
    if snap.disabled or not snap.tenant_active:
        return AuthResult(status_code=401, detail="account disabled")
    # token_invalidated_before: tokens issued before a password reset are stale.
    # Snapshots store naive UTC (utcnow_naive); normalize to a UTC timestamp for
    # the comparison against the int iat claim.
    invalidated = snap.token_invalidated_before
    if invalidated is not None:
        reset_ts = invalidated.replace(tzinfo=UTC).timestamp()
        if payload.get("iat", 0) < reset_ts:
            return AuthResult(status_code=401, detail="token invalidated")
    # Per-user fixed-window limit, counted only after verification succeeds.
    # fail-open: any Redis failure bypasses the limit (availability over
    # strictness) and is logged; over limit → 429 with rate-limit headers
    # (middleware passes them through).
    limiter = FixedWindowRateLimiter(redis=gredis.redis)   # lazy module singleton, read at call time
    try:
        allowed, rl_headers = await limiter.check(
            f"user:{payload['sub']}", limit=settings.user_rate_limit_per_minute,
            window_seconds=60)
        if not allowed:
            return AuthResult(status_code=429, detail="rate limit exceeded", headers=rl_headers)
    except Exception:
        logger.warning("user rate limit bypassed (redis down): sub=%s", payload["sub"])
    ctx = UserContext(user_id=payload["sub"], tenant_id=snap.tenant_id,
                      workspace_id=snap.workspace_id, roles=snap.roles)
    request.state.identity = ctx  # stub echo + downstream consumers (ctx object)
    identity_headers = {
        "x-user-id": ctx.user_id,
        "x-tenant-id": ctx.tenant_id,
        "x-workspace-id": ctx.workspace_id,
    }
    identity_headers.update(_profile_headers(request, ctx.user_id, ctx.tenant_id,
                                             ctx.workspace_id))
    return AuthResult(
        identity_headers=identity_headers,
        audit_event={
            "event_type": "auth.granted", "actor_id": ctx.user_id,
            "tenant_id": ctx.tenant_id,
            "target": f"{request.method} {request.url.path}", "result": "ok",
        },
    )


async def terminate_api_key(request: Request, deps: GatewayDeps) -> AuthResult:
    """API-key path (/v1/): snapshot (fail-closed) → QPS + daily quota →
    identity context + injected headers + audit event.

    The key acts on behalf of its creator: the actor everywhere (headers,
    audit) is the creator user_id, the same subject the enterprise internal
    token carries.
    """
    api_key = _extract_api_key(request)
    if api_key is None:
        return AuthResult(status_code=401, detail="missing api key")
    try:
        ctx = await deps.reader.get_api_key_snapshot(api_key)
    except SnapshotUnavailable:
        # Snapshot state unobtainable → reject (fail-closed)
        return AuthResult(status_code=401, detail="auth unavailable")
    if ctx is None:
        return AuthResult(status_code=401, detail="invalid api key")
    if ctx.user_id is None:
        # No creator subject (deleted creator / stale snapshot): reject fast —
        # an ak:* placeholder must never reach downstream services.
        return AuthResult(status_code=401, detail="invalid api key")
    if not ctx.rate_limit_disabled:
        for check, limit in ((deps.limiter.check_qps, ctx.rate_limit),
                             (deps.limiter.check_daily_requests, ctx.daily_request_limit)):
            if limit is None:
                continue
            try:
                allowed, headers = await check(ctx.api_key_id, limit)
            except SnapshotUnavailable:
                # Quota state unobtainable → reject (fail-closed): the SDK
                # limiter contract is "gateway converts to 401", never an
                # unhandled 500.
                return AuthResult(status_code=401, detail="auth unavailable")
            if not allowed:
                return AuthResult(status_code=429, detail="rate limit exceeded", headers=headers)
    request.state.identity = ctx               # /v1/stub echo (ctx object, not header strings)
    identity_headers = {
        "x-api-key-id": ctx.api_key_id,
        "x-tenant-id": ctx.tenant_id,
        "x-workspace-id": ctx.workspace_id,
    }
    identity_headers.update(_profile_headers(request, ctx.user_id, ctx.tenant_id,
                                             ctx.workspace_id))
    return AuthResult(
        identity_headers=identity_headers,
        audit_event={
            "event_type": "api_key.granted", "actor_id": ctx.user_id,
            "tenant_id": ctx.tenant_id,
            "target": f"{request.method} {request.url.path}", "result": "ok",
        },
    )


async def terminate(request: Request, deps: GatewayDeps) -> AuthResult:
    """Route by credential type: API_KEY_PATH_PREFIXES → API-key variant,
    everything else → user JWT variant (same split the strategies used)."""
    if request.url.path.startswith(settings.API_KEY_PATH_PREFIXES):
        return await terminate_api_key(request, deps)
    return await terminate_user(request, deps)
