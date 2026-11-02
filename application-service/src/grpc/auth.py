"""gRPC authentication for the application-service chat servicer.

direct mode: the gateway terminates credentials upstream and injects the
``X-App-*`` identity headers; metadata is the identity.
gateway mode: the internal token is verified by the enterprise handler and its
claims are authoritative; ``X-App-*`` metadata is context only, and a
client-supplied api key id must agree with the claims when both are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from memorybear_grpc.request import MetadataRequest
from pydantic import ValidationError

from ..api.dependencies import API_KEY_ID_HEADER, Principal, principal_from_headers
from ..auth import AppAuthConfig, _load_gateway_auth
from ..i18n import resolve_locale
from .envelope import abort_with, auth_rejection_content

APP_CHAT_ACL_METHOD = "POST"
APP_CHAT_ACL_PATH = "/internal/v1/app/chat"


@dataclass(frozen=True)
class AppChatPrincipal:
    """Identity for one chat call: the principal plus the api key binding id."""

    actor_id: UUID
    tenant_id: UUID
    workspace_id: UUID | None
    api_key_id: str | None


def build_chat_authenticator(config: AppAuthConfig, *, gateway: object | None = None):
    """Return the ``AuthInterceptor`` authenticator for this deployment mode."""

    handler = gateway
    if handler is None and config.auth_mode == "gateway":
        handler = _load_gateway_auth(config)

    async def authenticate(context, method: str) -> AppChatPrincipal:
        del method  # ACL is keyed by the HTTP-equivalent route, not the rpc path
        request = MetadataRequest(
            context.invocation_metadata(),
            method=APP_CHAT_ACL_METHOD,
            path=APP_CHAT_ACL_PATH,
        )
        language = resolve_locale(accepted=request.headers.get("accept-language"))

        async def reject(http_status: int, detail: str) -> None:
            await abort_with(
                context,
                http_status,
                auth_rejection_content(http_status, detail, language),
            )

        metadata_key_id = request.headers.get(API_KEY_ID_HEADER) or None
        if handler is None:
            # direct: the gateway is the only admitted source (NetworkPolicy)
            # and has already terminated credentials upstream.
            try:
                principal = principal_from_headers(request)
            except HTTPException as exc:
                await reject(exc.status_code, str(exc.detail))
                raise
            return AppChatPrincipal(
                actor_id=principal.actor_id,
                tenant_id=principal.tenant_id,
                workspace_id=principal.workspace_id,
                api_key_id=metadata_key_id,
            )
        try:
            claims = await handler.authenticate(request)
        except HTTPException as exc:
            await reject(exc.status_code, str(exc.detail))
            raise
        except Exception:
            await reject(401, "invalid token")
            raise
        if claims is None:
            # Single inbound channel: the application handler never passes a
            # request through (its legacy-header set is empty).
            await reject(401, "invalid token claims")
        claims_key_id = getattr(claims, "api_key_id", None) or None
        if claims_key_id and metadata_key_id and claims_key_id != metadata_key_id:
            # The token is authoritative; a disagreeing context header is a
            # forged or stale binding and must never reach the key gate.
            await reject(401, "api key id mismatch")
        try:
            principal = Principal(
                actor_id=claims.user_id,
                tenant_id=claims.tenant_id,
                workspace_id=claims.workspace_id or None,
            )
        except ValidationError:
            await reject(401, "invalid token claims")
        return AppChatPrincipal(
            actor_id=principal.actor_id,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            api_key_id=claims_key_id or metadata_key_id,
        )

    return authenticate


__all__ = [
    "APP_CHAT_ACL_METHOD",
    "APP_CHAT_ACL_PATH",
    "AppChatPrincipal",
    "build_chat_authenticator",
]
