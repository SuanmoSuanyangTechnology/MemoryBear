"""Channel registry service (model_channels): encryption orchestration + idempotent-merge registration primitives.

A pure registration-primitive layer (no wire surface), reused by data migration scripts
(legacy table → model_channels), the controller and the runtime registry. Wire semantics are
implemented above this service; model-ownership/impact translation is not part of it.

Encryption iron rule: newly written credentials are 100% ciphertext, envelope
v{ver}:{iv}:{tag}:{ct}, AAD = provider:tenant_id; plaintext is forbidden in any log/response/
exception; only credential_masked is exposed externally.
Transaction boundaries belong to the caller; this service only flushes.
"""
from __future__ import annotations

import base64
import uuid

from redbear_model.crypto import AESGCMEnvCipher, credential_sha256
from sqlalchemy.orm import Session

from ..config import current_settings
from ..utils.datetime_utils import to_timestamp_ms
from ..models.models_model import ModelChannel
from ..repositories.model_channel_repository import ModelChannelRepository
from ..utils.redis_cache import invalidate_runtime_model_info_batch

_CIPHER: AESGCMEnvCipher | None = None


def cipher_from_env() -> AESGCMEnvCipher:
    """MODEL_CREDENTIALS_KEY (base64 32B) → envelope cipher; process-wide singleton."""
    global _CIPHER
    if _CIPHER is None:
        raw = current_settings().model_credentials_key.get_secret_value().strip()
        if not raw:
            raise RuntimeError("MODEL_CREDENTIALS_KEY is not set (base64 32B master key)")
        try:
            _CIPHER = AESGCMEnvCipher(base64.b64decode(raw))
        except ValueError as exc:
            raise RuntimeError(f"MODEL_CREDENTIALS_KEY invalid: {exc}") from exc
    return _CIPHER


def _masked(api_key: str) -> str:
    if len(api_key) < 8:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"


def _aad(provider: str, tenant_id: uuid.UUID) -> str:
    return f"{provider}:{tenant_id}"


def describe_channel(row: ModelChannel) -> dict:
    """Redacted channel view (times in milliseconds) — shared by detail/list/delete impact preview (pure function, no queries)."""
    created_ms = to_timestamp_ms(row.created_at)
    updated_ms = to_timestamp_ms(row.updated_at)
    return {
        "id": str(row.id),
        "provider": row.provider,
        "tenant_id": str(row.tenant_id),
        "model_names": list(row.model_names or []),
        "is_provider_level": not row.model_names,
        "api_base": row.api_base,
        "credential_masked": row.credential_masked,
        "is_active": row.is_active,
        "priority": row.priority,
        "source": row.source,
        "remark": row.remark,
        "created_at_ms": created_ms,
        "updated_at_ms": updated_ms,
    }


class ChannelService:
    """model_channels registration primitives (sync Session).

    provider is always a real provider (composite is not allowed); named channels always have a
    non-empty model_names (clearing it deletes the row); [] only comes from provider-level
    registration, and a matching named row is upgraded in place; the idempotent merge key
    (provider, tenant_id, api_base, credential_sha256) is backed by a DB unique constraint
    (empty api_base = provider default endpoint).
    """

    def __init__(self, db: Session):
        self.db = db
        self.repo = ModelChannelRepository(db)
        self.cipher = cipher_from_env()

    # ---- Encryption ----
    def _protect(self, provider: str, tenant_id: uuid.UUID, api_key: str) -> tuple[str, str, str]:
        return (
            self.cipher.encrypt(api_key, aad=_aad(provider, tenant_id)),
            credential_sha256(api_key),
            _masked(api_key),
        )

    def reveal(self, channel: ModelChannel) -> str:
        """Decrypt the credential (the only decryption exit; used when fetching the key at runtime; never log or return it)."""
        return self.cipher.decrypt(
            channel.credential_encrypted, aad=_aad(channel.provider, channel.tenant_id)
        )

    # ---- Runtime cache invalidation (write paths invalidate eagerly; no reliance on TTL expiry) ----
    def _invalidate_runtime(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str,
        model_names: list[str] | None,
    ) -> None:
        """Two-layer invalidation: the in-process channel snapshot cache + the Redis runtime cache of affected configs.

        model_names=None → provider-level channel (affects every config of that provider);
        non-None → only configs whose name ∈ model_names. Invalidation happens right after the
        mutation is flushed. If the caller ends up rolling back: with no candidate probing in
        between it only costs one extra cache re-read; if probing already backfilled the snapshot
        cache from uncommitted state, the caller must invalidate again (see the delete/unbind
        handling in model_channel_service).
        """
        from .channel_registry import (  # Deferred import: registry depends back on this module's cipher
            affected_config_ids,
            invalidate_channel_cache,
        )

        invalidate_channel_cache(tenant_id, provider)
        config_ids = affected_config_ids(
            self.db, tenant_id=tenant_id, provider=provider, model_names=model_names
        )
        if config_ids:
            invalidate_runtime_model_info_batch(config_ids, tenant_id=tenant_id)

    # ---- Registration (idempotent merge, two entry points) ----
    def register_provider_channel(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        api_key: str,
        api_base: str | None = None,
        remark: str | None = None,
        priority: int = 0,
        source: str = "manual",
        extra: dict | None = None,
        created_by: uuid.UUID | None = None,
    ) -> tuple[ModelChannel, str]:
        """Register a provider-level public key (model_names=[], covering all unnamed models of that provider).

        api_base is always NULL (the provider's public base address is used at runtime; local
        providers are rejected upstream); when the idempotent key already exists: a named channel
        → upgraded in place to provider level (coverage expands to everything, returns
        "upgraded"); already provider-level → merged (no-op); otherwise a new row is created.
        Merge/upgrade never overwrite the existing row's other attributes (remark/priority/extra).
        Returns (channel, "created" | "merged" | "upgraded").
        """
        if provider == "composite":
            raise ValueError("composite is a config shape, not a channel provider")
        if isinstance(api_base, str) and not api_base.strip():
            api_base = None
        if api_base is not None:
            raise ValueError("provider-level channel must not set api_base")
        sha = credential_sha256(api_key)
        exist = self.repo.get_by_credential(
            provider=provider, tenant_id=tenant_id, api_base=api_base, credential_sha256=sha
        )
        if exist is not None:
            # Guard returning False = concurrently upgraded/deleted; return the current state, invalidation is the writer's job
            if exist.model_names and self.repo.promote_to_provider_level(exist.id):
                # A named row with the same credential matched (the unique constraint allows only one row): provider-level intent wins, upgrade in place
                self.db.refresh(exist)
                self._invalidate_runtime(
                    tenant_id=tenant_id, provider=provider, model_names=None
                )
                return exist, "upgraded"
            return exist, "merged"
        encrypted, _, masked = self._protect(provider, tenant_id, api_key)
        row = self.repo.create(
            tenant_id=tenant_id,
            provider=provider,
            model_names=[],
            api_base=api_base,
            credential_encrypted=encrypted,
            credential_sha256=sha,
            credential_masked=masked,
            source=source,
            remark=remark,
            created_by=created_by,
            priority=priority,
            extra=extra,
        )
        self._invalidate_runtime(tenant_id=tenant_id, provider=provider, model_names=None)
        return row, "created"

    def register_for_model(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        model_name: str,
        api_key: str,
        api_base: str | None = None,
        remark: str | None = None,
        priority: int = 0,
        created_by: uuid.UUID | None = None,
    ) -> tuple[ModelChannel, str]:
        """Add a model-specific key: stores into a named channel's model_names ∪ {model_name}.

        When the same credential+endpoint already exists, merged: a provider-level [] channel →
        the model is already covered by default, absorbed as-is (no-op); a named channel → no-op
        if already included, otherwise unioned in. Never produces a provider-level channel (a
        model-specific key only names itself).
        """
        if provider == "composite":
            raise ValueError("composite is a config shape, not a channel provider")
        sha = credential_sha256(api_key)
        exist = self.repo.get_by_credential(
            provider=provider, tenant_id=tenant_id, api_base=api_base, credential_sha256=sha
        )
        if exist is not None:
            if exist.model_names and model_name not in exist.model_names:
                self.repo.union_model_name(exist.id, model_name)
                self.db.refresh(exist)
                self._invalidate_runtime(
                    tenant_id=tenant_id, provider=provider, model_names=[model_name]
                )
            return exist, "merged"
        encrypted, _, masked = self._protect(provider, tenant_id, api_key)
        row = self.repo.create(
            tenant_id=tenant_id,
            provider=provider,
            model_names=[model_name],
            api_base=api_base,
            credential_encrypted=encrypted,
            credential_sha256=sha,
            credential_masked=masked,
            remark=remark,
            created_by=created_by,
            priority=priority,
        )
        self._invalidate_runtime(
            tenant_id=tenant_id, provider=provider, model_names=[model_name]
        )
        return row, "created"

    # ---- Unbind/delete ----
    def unbind_model(self, channel_id: uuid.UUID, model_name: str) -> tuple[bool, bool]:
        """Remove a model from a named channel: the row is auto-deleted once cleared.

        Returns (unbound, deleted): unbound=True means the coverage set really lost an entry;
        deleted=True means the named channel was thus cleared and its row deleted. A
        provider-level channel has no name to unbind → (False, False); missing row → LookupError.
        """
        row = self.repo.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        if not row.model_names:
            return False, False
        if not self.repo.unbind_model_name(channel_id, model_name):
            return False, False
        self._invalidate_runtime(
            tenant_id=row.tenant_id, provider=row.provider, model_names=[model_name]
        )
        fresh = self.repo.get(channel_id)
        if fresh is not None and not fresh.model_names:
            self.repo.delete_if_empty(channel_id)
            return True, True
        return True, False

    def delete(self, channel_id: uuid.UUID) -> bool:
        """Delete the credential itself (an explicit management action); returns False when missing. See describe for the impact preview.

        The flush makes the deletion immediately visible within the same transaction (a second
        delete → get returns empty → False).
        """
        row = self.repo.get(channel_id)
        if row is None:
            return False
        self.repo.delete(channel_id)
        self.db.flush()
        self._invalidate_runtime(
            tenant_id=row.tenant_id,
            provider=row.provider,
            model_names=None if not row.model_names else list(row.model_names),
        )
        return True

    # ---- Update ----
    def update_attributes(
        self,
        channel_id: uuid.UUID,
        *,
        priority: int | None = ...,
        remark: str | None = ...,
        api_base: str | None = ...,
        is_active: bool | None = ...,
        extra: dict | None = ...,
    ) -> ModelChannel:
        """Change channel attributes; omitted = unchanged; None semantics (clearing api_base / extra) per the repository's _UNSET docs.

        A provider-level channel's api_base is always NULL: empty strings normalize to None and
        non-empty values are rejected. extra is a full replacement (enterprise-semantics
        metadata), not exposed on the wire layer and used by internal callers only.
        """
        kwargs = {}
        for key, val in (
            ("priority", priority),
            ("remark", remark),
            ("api_base", api_base),
            ("is_active", is_active),
            ("extra", extra),
        ):
            if val is not ...:
                kwargs[key] = val
        if not kwargs:
            raise ValueError("no attribute to update")
        row = self.repo.get(channel_id)  # Invalidation context (the coverage set does not change with attributes)
        if row is not None and not row.model_names and "api_base" in kwargs:
            value = kwargs["api_base"]
            if isinstance(value, str) and not value.strip():
                kwargs["api_base"] = None
            if kwargs["api_base"] is not None:
                raise ValueError("provider-level channel must not set api_base")
        updated = self.repo.update_attributes(channel_id, **kwargs)
        if row is not None:
            self._invalidate_runtime(
                tenant_id=row.tenant_id,
                provider=row.provider,
                model_names=None if not row.model_names else list(row.model_names),
            )
        return updated

    def replace_credential(self, channel_id: uuid.UUID, *, api_key: str) -> ModelChannel:
        """Re-fill the credential: re-encrypt + recompute fingerprint/mask (AAD is taken from the row to prevent mismatches).

        If the key (api_base/fingerprint) change hits the unique constraint, the DB backs it up
        (IntegrityError is propagated to the caller).
        """
        row = self.repo.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        encrypted, sha, masked = self._protect(row.provider, row.tenant_id, api_key)
        updated = self.repo.update_credential(
            channel_id,
            credential_encrypted=encrypted,
            credential_sha256=sha,
            credential_masked=masked,
        )
        self._invalidate_runtime(
            tenant_id=row.tenant_id,
            provider=row.provider,
            model_names=None if not row.model_names else list(row.model_names),
        )
        return updated

    # ---- Read/impact ----
    def get(self, channel_id: uuid.UUID) -> ModelChannel | None:
        return self.repo.get(channel_id)

    def get_by_credential(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        api_base: str | None,
        credential_sha256: str,
    ) -> ModelChannel | None:
        return self.repo.get_by_credential(
            provider=provider, tenant_id=tenant_id, api_base=api_base, credential_sha256=credential_sha256
        )

    def list_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str | None = None,
        is_active: bool | None = None,
        source: str | None = None,
        model_name: str | None = None,
    ) -> list[ModelChannel]:
        return self.repo.list_tenant(
            tenant_id=tenant_id,
            provider=provider,
            is_active=is_active,
            source=source,
            model_name=model_name,
        )

    def describe(self, channel_id: uuid.UUID) -> dict | None:
        """Channel detail (redacted view, times in milliseconds) — shared by the delete/impact preview and list display."""
        row = self.repo.get(channel_id)
        if row is None:
            return None
        return describe_channel(row)


__all__ = ["ChannelService", "describe_channel"]
