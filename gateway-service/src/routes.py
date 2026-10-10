"""Stub and catch-all forwarding: /stub and /v1/stub echo the identity context
(e2e asserts credential stripping / identity-header injection); any other
unregistered path is resolved to a target route by the middleware and forwarded
to the target service by the Forwarder.

/stub is the user-JWT stub (/api/ semantics), /v1/stub the API-key stub (/v1/
external-integration traffic) — the middleware routes them via
API_KEY_PATH_PREFIXES; these handlers only echo the injected identity.
"""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from auth_sdk.schema import UserContext, ApiKeyContext
from src.forward import Forwarder

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


def _stub_identity(request: Request, expected: type) -> object | None:
    """Echo helper for the stub endpoints. The middleware's termination pipeline
    writes request.state.identity for both strategies (direct and gateway); a
    missing or mismatched context here is a wiring error, surfaced by callers as
    an explicit 400 instead of a 500 AttributeError."""
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, expected):
        return None
    return identity


def _echo_headers(request: Request) -> dict[str, str]:
    """Echo every x-* header after termination (stripped credentials are gone,
    injected identity headers — generic trio plus a resolved route's service
    profile — are visible) for e2e/unit assertions."""
    return {k.lower(): v for k, v in request.headers.items()
            if k.lower().startswith("x-")}


@router.get("/stub")
async def stub(request: Request):
    identity = _stub_identity(request, UserContext)
    if identity is None:
        return JSONResponse(status_code=400, content={
            "error": "expects user identity",
            "detail": "identity not resolved: termination pipeline did not attach "
                      "a user identity context"})
    return {
        "user_id": identity.user_id, "tenant_id": identity.tenant_id,
        "workspace_id": identity.workspace_id,
        "internal_token": getattr(request.state, "internal_token", None),
        "has_user_authorization": "authorization" in request.headers,
        "x_headers": _echo_headers(request),
    }


@router.get("/v1/stub")
async def api_key_stub(request: Request):
    identity = _stub_identity(request, ApiKeyContext)
    if identity is None:
        return JSONResponse(status_code=400, content={
            "error": "expects api key identity",
            "detail": "identity not resolved: termination pipeline did not attach "
                      "an api key identity context"})
    return {
        "api_key_id": identity.api_key_id,
        "tenant_id": identity.tenant_id,
        "workspace_id": identity.workspace_id,
        "scopes": identity.scopes,
        "internal_token": getattr(request.state, "internal_token", None),
        "has_x_api_key": "x-api-key" in request.headers,   # stripped → False
        "x_headers": _echo_headers(request),
    }


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
async def forward_route(request: Request):
    route = getattr(request.state, "target_route", None)
    if route is None:
        logger.warning("unmatched route: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=404, content={"detail": "not found"})
    forwarder: Forwarder = request.app.state.forwarder
    return await forwarder.forward(request, route)
