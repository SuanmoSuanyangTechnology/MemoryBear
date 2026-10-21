"""Reusable service-to-service authentication for Memory modules."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from importlib import import_module
from importlib.util import find_spec
from typing import Literal
from uuid import UUID

from auth_sdk.schema import UserContext
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.common.errors import BizCode
from src.common.responses import failure
from src.i18n import get_request_language, t
from src.middleware.request_logging import record_request_failure

logger = logging.getLogger(__name__)

MemoryAuthSource = Literal["ex_api", "in_api"]

SOURCE_HEADER = "x-source"
SOURCE_EXTERNAL = "ex_api"
SOURCE_INTERNAL = "in_api"

ACTOR_HEADER = "x-user-id"
API_KEY_ID_HEADER = "x-api-key-id"
TENANT_HEADER = "x-tenant-id"
WORKSPACE_HEADER = "x-workspace-id"

ActorKind = Literal["actor", "apikey"]


@dataclass(frozen=True, slots=True)
class MemoryPrincipal:
    actor_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    #: The face the caller arrived on — the value of the ``x-source`` header, which
    #: is why the field carries that header's name.
    source: MemoryAuthSource
    actor_kind: ActorKind
    trace_id: str


@dataclass(frozen=True, slots=True)
class MemoryAuthConfig:
    """Host-provided policy for all current and future Memory modules."""

    auth_mode: Literal["direct", "gateway"] = "direct"
    service_name: str = "memory-profile"
    public_paths: frozenset[str] = field(default_factory=frozenset)
    kill_switch_file: str | None = None
    jwks_url: str | None = None
    redis: object | None = None
    # Deployment-scoped ACL names; app.py passes DB_NAME-derived values. The
    # defaults match the legacy global names for direct construction in tests.
    acl_rules_key: str = "acl:rules"
    acl_rules_changed_channel: str = "acl:rules:changed"


def load_gateway_auth(config: MemoryAuthConfig):
    """Load the private Memory provider without a direct-mode fallback."""

    module_name = "enterprise_ext.memory"
    try:
        spec = find_spec(module_name)
    except ModuleNotFoundError:
        spec = None
    if spec is None:
        raise RuntimeError(
            "MEMORY_AUTH_MODE=gateway requires the private 'enterprise-extensions' package"
        )
    module = import_module(module_name)
    try:
        provider = module.MemoryGatewayAuth
    except AttributeError as exc:
        raise RuntimeError("enterprise_ext.memory must export MemoryGatewayAuth") from exc
    return provider(config)


def _auth_error(request: Request, status_code: int, detail: object) -> JSONResponse:
    code = {
        401: BizCode.UNAUTHORIZED,
        403: BizCode.WORKSPACE_ACCESS_DENIED,
    }.get(status_code, BizCode.INTERNAL_ERROR)
    trace_id = getattr(request.state, "trace_id", "")
    record_request_failure(
        request,
        response_code=code,
        error_code="AUTHENTICATION_REJECTED",
    )
    return JSONResponse(
        status_code=status_code,
        content=failure(
            code, t("common.auth.failed", get_request_language(request)), str(detail)
        ).model_dump(mode="json"),
        headers={"X-Trace-Id": trace_id} if trace_id else None,
    )


def _uuid_header(request: Request, name: str) -> UUID:
    value = request.headers.get(name)
    if not value:
        raise ValueError(f"missing {name}")
    return UUID(value)


def _direct_actor(request: Request) -> tuple[UUID, ActorKind]:
    """The acting id and which header named it.

    ``x-user-id`` when the terminator resolved a user; otherwise the API-key face's
    ``x-api-key-id``, whose value is the key rather than the user behind it. Neither
    present is a 401 — analytics cannot run without an id to authorize against.
    """

    user_id = request.headers.get(ACTOR_HEADER)
    if user_id:
        return UUID(user_id), "actor"
    api_key_id = request.headers.get(API_KEY_ID_HEADER)
    if api_key_id:
        return UUID(api_key_id), "apikey"
    raise ValueError(f"missing {ACTOR_HEADER}")


def _direct_principal(request: Request) -> MemoryPrincipal:
    source = request.headers.get(SOURCE_HEADER)
    if source not in {SOURCE_EXTERNAL, SOURCE_INTERNAL}:
        raise ValueError(f"invalid {SOURCE_HEADER}")
    actor_id, actor_kind = _direct_actor(request)
    return MemoryPrincipal(
        actor_id=actor_id,
        tenant_id=_uuid_header(request, TENANT_HEADER),
        workspace_id=_uuid_header(request, WORKSPACE_HEADER),
        source=source,
        actor_kind=actor_kind,
        trace_id=getattr(request.state, "trace_id", ""),
    )


def _claims_principal(request: Request, context: UserContext) -> MemoryPrincipal:
    # ``context.auth_type`` is auth-sdk's own token claim (``api_key`` or absent),
    # a different vocabulary from the ``x-source`` face value below; it is only
    # read here, never renamed.
    if context.auth_type not in {None, "api_key"}:
        raise ValueError("invalid auth_type claim")
    if context.auth_type == "api_key" and "memory" not in context.scopes:
        raise HTTPException(status_code=403, detail="memory scope required")
    source: MemoryAuthSource = (
        SOURCE_EXTERNAL if context.auth_type == "api_key" else SOURCE_INTERNAL
    )

    api_key_id = getattr(context, "api_key_id", None)
    actor_id: UUID = UUID(api_key_id) if api_key_id else UUID(context.user_id)
    actor_kind: ActorKind = "apikey" if api_key_id else "actor"
    return MemoryPrincipal(
        actor_id=actor_id,
        tenant_id=UUID(context.tenant_id),
        workspace_id=UUID(context.workspace_id),
        source=source,
        actor_kind=actor_kind,
        trace_id=getattr(request.state, "trace_id", ""),
    )


class MemoryAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate all non-public paths for every Memory domain module."""

    def __init__(
        self,
        app,
        *,
        config: MemoryAuthConfig,
        gateway_auth: object | None = None,
    ) -> None:
        super().__init__(app)
        self._config = config
        self._gateway = gateway_auth

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._config.public_paths:
            return await call_next(request)

        try:
            if self._kill_switch_active():
                logger.warning("memory auth kill switch active; using trusted direct headers")
                principal = _direct_principal(request)
            elif self._config.auth_mode == "gateway":
                principal = await self._gateway_principal(request)
            else:
                principal = _direct_principal(request)
        except HTTPException as exc:
            return _auth_error(request, exc.status_code, exc.detail)
        except (TypeError, ValueError, AttributeError) as exc:
            logger.warning("memory principal rejected: %s", type(exc).__name__)
            return _auth_error(request, 401, "invalid principal")
        except Exception as exc:
            logger.exception("memory authentication failed", exc_info=exc)
            return _auth_error(request, 401, "invalid token")

        request.state.memory_context = principal
        return await call_next(request)

    async def _gateway_principal(self, request: Request) -> MemoryPrincipal:
        if self._gateway is None:
            raise HTTPException(status_code=500, detail="auth misconfigured")
        context = await self._gateway.authenticate(request)
        if context is None:
            return _direct_principal(request)
        return _claims_principal(request, context)

    def _kill_switch_active(self) -> bool:
        path = self._config.kill_switch_file
        return bool(path and os.path.exists(path))


__all__ = [
    "ACTOR_HEADER",
    "API_KEY_ID_HEADER",
    "ActorKind",
    "MemoryAuthConfig",
    "MemoryAuthMiddleware",
    "MemoryPrincipal",
    "MemoryAuthSource",
    "SOURCE_HEADER",
    "SOURCE_EXTERNAL",
    "SOURCE_INTERNAL",
    "TENANT_HEADER",
    "WORKSPACE_HEADER",
    "load_gateway_auth",
]
