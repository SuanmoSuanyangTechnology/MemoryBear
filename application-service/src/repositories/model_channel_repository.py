"""Data access for the model_channels registry table.

The idempotent merge key (provider, tenant_id, api_base, credential_sha256) is enforced by a DB
unique constraint; all write primitives are single-statement atomic operations
(union/unbind/delete_if_empty), avoiding read-modify-write races. Transaction boundaries belong to
the caller (service layer/migration scripts); this repository only flushes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from ..utils.datetime_utils import utcnow_naive
from ..models.models_model import ModelChannel

# Sentinel for api_base in update_attributes: an explicit None means "set to NULL", _UNSET means "leave unchanged"
_UNSET = object()


class ModelChannelRepository:
    def __init__(self, db: Session):
        self.db = db

    # ---- Read ----
    def get(self, channel_id: uuid.UUID) -> ModelChannel | None:
        return self.db.execute(
            select(ModelChannel).where(ModelChannel.id == channel_id)
        ).scalars().first()

    def get_by_credential(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        api_base: str | None,
        credential_sha256: str,
    ) -> ModelChannel | None:
        stmt = select(ModelChannel).where(
            ModelChannel.provider == provider,
            ModelChannel.tenant_id == tenant_id,
            ModelChannel.credential_sha256 == credential_sha256,
        )
        if api_base is None:
            stmt = stmt.where(ModelChannel.api_base.is_(None))
        else:
            stmt = stmt.where(ModelChannel.api_base == api_base)
        return self.db.execute(stmt).scalars().first()

    def list_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str | None = None,
        is_active: bool | None = None,
        source: str | None = None,
        model_name: str | None = None,
    ) -> list[ModelChannel]:
        stmt = select(ModelChannel).where(ModelChannel.tenant_id == tenant_id)
        if provider is not None:
            stmt = stmt.where(ModelChannel.provider == provider)
        if is_active is not None:
            stmt = stmt.where(ModelChannel.is_active.is_(is_active))
        if source is not None:
            stmt = stmt.where(ModelChannel.source == source)
        rows = list(self.db.execute(stmt).scalars().all())
        if model_name is None:
            return rows
        # covers semantics: [] = provider-level, covering all unnamed models; named = only the listed model names
        return [ch for ch in rows if not ch.model_names or model_name in ch.model_names]

    def exists_covering(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str,
        model_name: str | None = None,
        source: str | None = None,
    ) -> bool:
        """Whether a channel covering the target (model-level or provider-level) exists, **without filtering by is_active**.

        Used as the pre-enable check: distinguishes "channels exist but all disabled" from
        "no channel was ever registered".
        """
        conditions = [
            ModelChannel.tenant_id == tenant_id,
            ModelChannel.provider == provider,
        ]
        if source is not None:
            conditions.append(ModelChannel.source == source)
        if model_name is not None:
            conditions.append(
                or_(
                    ModelChannel.model_names == text("'[]'::jsonb"),
                    ModelChannel.model_names.has_key(model_name),
                )
            )
        stmt = select(ModelChannel.id).where(*conditions).limit(1)
        return self.db.execute(stmt).first() is not None

    # ---- Write ----
    def create(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str,
        model_names: list[str],
        api_base: str | None,
        credential_encrypted: str,
        credential_sha256: str,
        credential_masked: str,
        source: str = "manual",
        remark: str | None = None,
        created_by: uuid.UUID | None = None,
        priority: int = 0,
        extra: dict | None = None,
    ) -> ModelChannel:
        row = ModelChannel(
            tenant_id=tenant_id,
            provider=provider,
            model_names=list(model_names),
            api_base=api_base,
            credential_encrypted=credential_encrypted,
            credential_sha256=credential_sha256,
            credential_masked=credential_masked,
            priority=priority,
            source=source,
            extra=extra or {},
            remark=remark,
            created_by=created_by,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def union_model_name(self, channel_id: uuid.UUID, model_name: str) -> bool:
        """Union of a named channel with {model_name}: the jsonb ? scalar membership test guards against duplicate inserts."""
        result = self.db.execute(
            update(ModelChannel)
            .where(
                ModelChannel.id == channel_id,
                ~ModelChannel.model_names.has_key(model_name),
            )
            .values(
                model_names=ModelChannel.model_names.concat(
                    func.jsonb_build_array(model_name)
                ),
                updated_at=utcnow_naive(),
            )
        )
        return result.rowcount > 0

    def promote_to_provider_level(self, channel_id: uuid.UUID) -> bool:
        """Named channel → provider level: clears model_names (single-statement whole replacement, no read-modify-write).

        The '[]' guard makes rows already at provider level a no-op (prevents concurrent/duplicate promotion).
        """
        result = self.db.execute(
            update(ModelChannel)
            .where(
                ModelChannel.id == channel_id,
                ModelChannel.model_names != text("'[]'::jsonb"),
            )
            .values(model_names=[], updated_at=utcnow_naive())
        )
        return result.rowcount > 0

    def unbind_model_name(self, channel_id: uuid.UUID, model_name: str) -> bool:
        """Remove a model name from a named channel (single-statement rebuild without the element, no read-modify-write).

        The jsonb ? membership guard ensures a no-op when the name is not in the coverage set,
        so the column is never rewritten wholesale.
        """
        result = self.db.execute(
            text(
                """
                UPDATE model_channels
                SET model_names = COALESCE(
                        (SELECT jsonb_agg(elem)
                           FROM jsonb_array_elements_text(model_channels.model_names) AS e(elem)
                          WHERE elem <> :model_name),
                        '[]'::jsonb),
                    updated_at = :ts
                WHERE id = :channel_id
                  AND model_names ? :model_name
                """
            ),
            {"model_name": model_name, "ts": utcnow_naive(), "channel_id": channel_id},
        )
        if result.rowcount > 0:
            # Raw text SQL bypasses the ORM and does not sync the identity map, so expire loaded instances to avoid stale reads
            self.db.expire_all()
        return result.rowcount > 0

    def delete_if_empty(self, channel_id: uuid.UUID) -> bool:
        """Delete a named channel row once cleared; the '[]' guard prevents races with a concurrent union (if the union commits first, this statement is a no-op)."""
        result = self.db.execute(
            delete(ModelChannel).where(
                ModelChannel.id == channel_id,
                ModelChannel.model_names == text("'[]'::jsonb"),
            )
        )
        return result.rowcount > 0

    def delete(self, channel_id: uuid.UUID) -> None:
        row = self.db.get(ModelChannel, channel_id)
        if row is not None:
            self.db.delete(row)

    def update_attributes(
        self,
        channel_id: uuid.UUID,
        *,
        priority: int | None = _UNSET,
        remark: str | None = _UNSET,
        api_base: str | None = _UNSET,   # _UNSET=unchanged; None=clear to NULL; str=update the endpoint
        is_active: bool | None = _UNSET,
        extra: dict | None = _UNSET,     # _UNSET=unchanged; dict=full replacement (enterprise-semantics metadata)
    ) -> ModelChannel:
        values: dict = {}
        if priority is not _UNSET:
            values["priority"] = priority
        if remark is not _UNSET:
            values["remark"] = remark
        if api_base is not _UNSET:
            values["api_base"] = api_base
        if is_active is not _UNSET:
            values["is_active"] = is_active
        if extra is not _UNSET:
            values["extra"] = extra if extra is not None else {}
        if values:
            values["updated_at"] = utcnow_naive()
            self.db.execute(
                update(ModelChannel).where(ModelChannel.id == channel_id).values(**values)
            )
        row = self.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        return row

    def update_credential(
        self,
        channel_id: uuid.UUID,
        *,
        credential_encrypted: str,
        credential_sha256: str,
        credential_masked: str,
    ) -> ModelChannel:
        self.db.execute(
            update(ModelChannel)
            .where(ModelChannel.id == channel_id)
            .values(
                credential_encrypted=credential_encrypted,
                credential_sha256=credential_sha256,
                credential_masked=credential_masked,
                updated_at=utcnow_naive(),
            )
        )
        row = self.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        return row


__all__ = ["ModelChannelRepository"]
