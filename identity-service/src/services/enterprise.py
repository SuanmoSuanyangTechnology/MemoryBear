"""Lazy adapter to ``enterprise_ext.identity`` (community builds load nothing).

Enterprise mode must fail closed for per-user permissions: an SSO-bound user
without the enterprise package loaded gets an empty permission list rather
than the local-account "all". Registration is a module-level side effect of
``src.main``; a missing package registers nothing (community behavior).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.plugins import get_plugin

SSO_PERMISSIONS_PLUGIN = "sso_permissions_provider"


def register_identity_plugins() -> None:
    """Register enterprise plugins; community builds have nothing to register."""

    try:
        from enterprise_ext.identity import register_identity_plugins as register
    except ImportError:
        return
    register()


def user_permissions(db: Session, external_source: str | None) -> list[str]:
    """Resolve the permission list surfaced in auth/user payloads.

    Local accounts keep the monolith's ``["all"]``; SSO-bound accounts read
    the source's permissions through the enterprise provider. Without the
    provider the list is empty (fail-closed).
    """

    if not external_source:
        return ["all"]
    provider = get_plugin(SSO_PERMISSIONS_PLUGIN)
    if provider is None:
        return []
    return list(provider(db, external_source) or [])
