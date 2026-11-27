"""End-user identity and info updates (frozen port of the identity-side halves
of the monolith ``EndUserService.confirm_identity`` and
``UserMemoryService.update_end_user_info_async``).

An identity-merge hit executes locally: this service decides the merge pair
under the identity advisory lock and delegates the merge itself (memory write
locks, graph/RAG reassignment, reflection dispatch) to memory-profile's
internal merge endpoint. An alias change on info update forwards to
memory-profile's internal info-update face: it owns the alias graph sync and
writes the info row itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..integrations.memory_profile import MemoryProfileClient
from ..models.end_user_model import EndUser
from ..repositories.end_user_info_repository import EndUserInfoRepository
from ..repositories.end_user_repository import EndUserRepository
from ..trace import get_trace_id
from ..utils.datetime_utils import to_timestamp_ms, utcnow_naive

logger = logging.getLogger(__name__)

# Placeholder display names copied verbatim from the memory-domain dedup module
# (`_USER_PLACEHOLDER_NAMES`); the update path drops them.
_USER_PLACEHOLDER_NAMES = {"用户", "我", "user", "i"}


def _is_user_placeholder_name(name: str) -> bool:
    """True when the name is a placeholder ("用户" / "我" / "User" / "I"),
    ignoring surrounding whitespace and case (the set is all lowercase,
    matching the extraction-side comparison)."""

    return name.strip().lower() in _USER_PLACEHOLDER_NAMES


def _normalize_other_name(value: Any) -> str | None:
    """Normalize an incoming other_name; None drops the field.

    Returns the stripped name for a valid string, None for None / non-string /
    empty / whitespace-only / placeholder values so the caller never clears or
    pollutes the stored name.
    """

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if _is_user_placeholder_name(normalized):
        logger.warning("Rejecting placeholder other_name: %s", normalized)
        return None
    return normalized


_INFO_ALLOWED_FIELDS = {"other_name", "aliases"}


@dataclass(frozen=True)
class IdentityOutcome:
    """Result of an identity update: a local confirm/clear, or a merge whose
    memory-side effects ran inside memory-profile."""

    final_id: UUID
    identity_status: str
    merged: bool = False


@dataclass(frozen=True)
class InfoUpdateOutcome:
    """Local info update result (``data``/``error``) or the order to forward
    (an alias change carries a memory-side sync)."""

    forward: bool = False
    data: dict[str, Any] | None = None
    error: str | None = None


class EndUserService:
    def __init__(self, db: Session, *, memory_profile: MemoryProfileClient | None = None):
        self.db = db
        self.memory_profile = memory_profile
        self.user_repo = EndUserRepository(db)
        self.info_repo = EndUserInfoRepository(db)

    def confirm_identity(
        self,
        workspace_id: UUID,
        current_end_user: EndUser,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        identity_features: str | None = None,
    ) -> IdentityOutcome:
        """Confirm or clear the cross-channel identity of one end user.

        - identifier present -> confirmed (long-lived); absent -> temporary.
        - an active row already holding the identifier means the current user
          must merge into it: the merge runs inside memory-profile (it owns
          the memory write locks and derived state), this service only decides
          the pair and records the outcome.

        The identifier lookup runs under the transaction-scoped advisory lock,
        so "lookup -> decide" stays serialized; the lock is released before
        the merge call because HTTP must not run inside a database
        transaction.
        """

        has_id = bool(identity_features and identity_features.strip())
        clean_features = identity_features.strip() if has_id else None
        identity_status = "confirmed" if has_id else "temporary"

        if has_id:
            self.user_repo.acquire_identity_lock(workspace_id, clean_features)
            candidate = self.user_repo.find_active_by_identity_features(
                workspace_id, clean_features, for_update=False
            )
            if candidate is not None and candidate.id != current_end_user.id:
                return self._merge_into(
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    source=current_end_user,
                    target=candidate,
                    identity_features=clean_features,
                )

        current_end_user.identity_features = clean_features
        current_end_user.identity_status = identity_status
        self.db.commit()
        self.db.refresh(current_end_user)
        return IdentityOutcome(
            final_id=current_end_user.id,
            identity_status=identity_status,
            merged=False,
        )

    def _merge_into(
        self,
        *,
        workspace_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        source: EndUser,
        target: EndUser,
        identity_features: str,
    ) -> IdentityOutcome:
        """Merge ``source`` into ``target`` through memory-profile.

        The advisory lock is released before the call: the merge service
        acquires its own memory write locks, and the HTTP hop must not run
        inside a database transaction. The audit write happens only after the
        merge succeeded — a failed merge then leaves no committed local state
        and a retry re-runs cleanly, while after a successful merge the origin
        row is soft-deleted and later retries resolve to the target.
        """

        source_id = source.id
        target_id = target.id
        self.db.rollback()

        if self.memory_profile is None:
            raise BusinessException(
                "memory profile merge client is not configured", BizCode.INTERNAL_ERROR
            )
        envelope = self.memory_profile.merge_users(
            source_end_user_id=source_id,
            target_end_user_id=target_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            trace_id=get_trace_id(),
        )
        if envelope.get("code") != 0:
            raise BusinessException(
                "memory profile merge rejected: "
                f"code={envelope.get('code')} msg={envelope.get('msg')} "
                f"error={envelope.get('error')}",
                BizCode.INTERNAL_ERROR,
            )

        self._record_identity_merge(source, source_id, identity_features)
        return IdentityOutcome(
            final_id=target_id, identity_status="confirmed", merged=True
        )

    def _record_identity_merge(
        self, source: EndUser, source_id: UUID, identity_features: str
    ) -> None:
        """Best-effort audit: the merged-away row keeps the identifier it was
        confirmed with (the row state the monolith leaves behind).

        A failure here is logged, never surfaced: the merge already committed
        in memory-profile, so failing the request would invite a retry that
        resolves to the target anyway.
        """

        try:
            source.identity_features = identity_features
            source.identity_status = "confirmed"
            source.updated_at = utcnow_naive()
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.warning(
                "Identity merge audit write failed for end user %s",
                source_id,
                exc_info=True,
            )

    def update_end_user_info(
        self, end_user_id: str, raw_update_data: dict[str, Any]
    ) -> InfoUpdateOutcome:
        """Update the end-user info record (PG side only).

        Returns the forward order when the (filtered) alias list differs from
        the stored one: the alias sync into the memory storage happens inside
        memory-profile, which then runs the whole update. The caller has
        already resolved ``end_user_id`` to the merge target.
        """

        try:
            user_uuid = UUID(end_user_id)
        except ValueError:
            return InfoUpdateOutcome(error="无效的终端用户ID格式")

        record = self.info_repo.get_end_user_info(user_uuid)
        if not record:
            return InfoUpdateOutcome(error="终端用户信息记录不存在")

        update_data = dict(raw_update_data)
        # Normalize other_name before the change comparison, exactly like the
        # host: None / empty / whitespace-only / placeholder values are dropped
        # (never clear or pollute the stored name); valid names are stripped.
        if "other_name" in update_data:
            normalized_name = _normalize_other_name(update_data["other_name"])
            if normalized_name is None:
                update_data.pop("other_name")
            else:
                update_data["other_name"] = normalized_name

        if "aliases" in update_data and update_data["aliases"]:
            # Each alias is stripped once; filtering and output share the value.
            update_data["aliases"] = [
                stripped
                for alias in update_data["aliases"]
                if isinstance(alias, str)
                and (stripped := alias.strip())
                and not _is_user_placeholder_name(stripped)
            ]

        # meta_data is owned by the extraction pipeline and cannot be updated through
        # this endpoint: GET returns a display projection, and writing it back
        # wholesale would clear stored data such as domain_specific_info.
        update_data.pop("meta_data", None)

        aliases_updated = (
            "aliases" in update_data and update_data["aliases"] != record.aliases
        )
        other_name_updated = (
            "other_name" in update_data
            and update_data["other_name"] != record.other_name
        )

        if aliases_updated:
            self.db.rollback()
            return InfoUpdateOutcome(forward=True)

        for field, value in update_data.items():
            if field in _INFO_ALLOWED_FIELDS:
                setattr(record, field, value)
        record.updated_at = utcnow_naive()

        if other_name_updated:
            end_user = self.user_repo.get_end_user_by_id(user_uuid)
            if end_user:
                end_user.other_name = update_data["other_name"]
                end_user.updated_at = utcnow_naive()
            else:
                logger.warning("End user row missing for info update: %s", end_user_id)

        self.db.commit()
        self.db.refresh(record)

        return InfoUpdateOutcome(
            forward=False,
            data={
                "end_user_info_id": str(record.id),
                "end_user_id": str(record.end_user_id),
                "other_name": record.other_name,
                "aliases": record.aliases,
                "meta_data": record.meta_data_new if isinstance(record.meta_data_new, dict) else {},
                "created_at": to_timestamp_ms(record.created_at),
                "updated_at": to_timestamp_ms(record.updated_at),
            },
        )


__all__ = ["EndUserService", "IdentityOutcome", "InfoUpdateOutcome"]
