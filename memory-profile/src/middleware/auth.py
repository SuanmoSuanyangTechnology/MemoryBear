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
from src.middleware.request_logging import record_request_failure

logger = logging.getLogger(__name__)
MemorySource = Literal["manager", "api_key"]
_DEFAULT_HEADER_PREFIX = "X-Memory"


@dataclass(frozen=True, slots=True)
class MemoryPrincipal:
    actor_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    source: MemorySource
    trace_id: str


@dataclass(frozen=True, slots=True)
class MemoryAuthConfig:
    """Host-provided policy for all current and future Memory modules."""

    auth_mode: Literal["direct", "gateway"] = "direct"
    service_name: str = "memory-profile"
    header_prefix: str = _DEFAULT_HEADER_PREFIX
    public_paths: frozenset[str] = field(default_factory=frozenset)
    kill_switch_file: str | None = None
    jwks_url: str | None = None
    redis: object | None = None


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
        content=failure(code, "认证失败", str(detail)).model_dump(mode="json"),
        headers={"X-Trace-Id": trace_id} if trace_id else None,
    )


def _identity_header(config: MemoryAuthConfig, suffix: str) -> str:
    return f"{config.header_prefix}-{suffix}"


def _uuid_header(request: Request, name: str) -> UUID:
    value = request.headers.get(name)
    if not value:
        raise ValueError(f"missing {name}")
    return UUID(value)


def _direct_principal(request: Request, config: MemoryAuthConfig) -> MemoryPrincipal:
    source_header = _identity_header(config, "Source")
    source = request.headers.get(source_header)
    if source not in {"manager", "api_key"}:
        raise ValueError(f"invalid {source_header}")
    return MemoryPrincipal(
        actor_id=_uuid_header(request, _identity_header(config, "Actor-ID")),
        tenant_id=_uuid_header(request, _identity_header(config, "Tenant-ID")),
        workspace_id=_uuid_header(request, _identity_header(config, "Workspace-ID")),
        source=source,
        trace_id=getattr(request.state, "trace_id", ""),
    )


def _claims_principal(request: Request, context: UserContext) -> MemoryPrincipal:
    if context.auth_type not in {None, "api_key"}:
        raise ValueError("invalid auth_type claim")
    if context.auth_type == "api_key" and "memory" not in context.scopes:
        raise HTTPException(status_code=403, detail="memory scope required")
    source: MemorySource = "api_key" if context.auth_type == "api_key" else "manager"
    return MemoryPrincipal(
        actor_id=UUID(context.user_id),
        tenant_id=UUID(context.tenant_id),
        workspace_id=UUID(context.workspace_id),
        source=source,
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
                principal = _direct_principal(request, self._config)
            elif self._config.auth_mode == "gateway":
                principal = await self._gateway_principal(request)
            else:
                principal = _direct_principal(request, self._config)
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
            return _direct_principal(request, self._config)
        return _claims_principal(request, context)

    def _kill_switch_active(self) -> bool:
        path = self._config.kill_switch_file
        return bool(path and os.path.exists(path))


__all__ = [
    "MemoryAuthConfig",
    "MemoryAuthMiddleware",
    "MemoryPrincipal",
    "MemorySource",
    "load_gateway_auth",
]
