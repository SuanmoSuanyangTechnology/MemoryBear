"""End-user identity and info updates (frozen port of the identity-side halves
of the monolith ``EndUserService.confirm_identity`` and
``UserMemoryService.update_end_user_info_async``).

Memory-side effects are not executed here: an identity-merge hit and an alias
change both delegate to the monolith ``/v1`` face (cross-domain write forward),
so this module only performs the PG-only paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from ..models.end_user_model import EndUser
from ..repositories.end_user_info_repository import EndUserInfoRepository
from ..repositories.end_user_repository import EndUserRepository
from ..utils.datetime_utils import to_timestamp_ms, utcnow_naive

logger = logging.getLogger(__name__)

# Placeholder display names copied verbatim from the memory-domain dedup module
# (`_USER_PLACEHOLDER_NAMES`); the update path drops them.
_USER_PLACEHOLDER_NAMES = {"用户", "我", "user", "i"}

_INFO_ALLOWED_FIELDS = {"other_name", "aliases", "meta_data"}


@dataclass(frozen=True)
class IdentityOutcome:
    """Result of a local identity update, or the order to forward the request
    (merge hit: memory-side effects must run on the monolith)."""

    forward: bool
    final_id: UUID | None = None
    identity_status: str | None = None
    merged: bool = False


@dataclass(frozen=True)
class InfoUpdateOutcome:
    """Local info update result (``data``/``error``) or the order to forward
    (an alias change carries a memory-side sync)."""

    forward: bool = False
    data: dict[str, Any] | None = None
    error: str | None = None


class EndUserService:
    def __init__(self, db: Session):
        self.db = db
        self.user_repo = EndUserRepository(db)
        self.info_repo = EndUserInfoRepository(db)

    def confirm_identity(
        self,
        workspace_id: UUID,
        current_end_user: EndUser,
        identity_features: str | None = None,
    ) -> IdentityOutcome:
        """Confirm or clear the cross-channel identity of one end user.

        - identifier present -> confirmed (long-lived); absent -> temporary.
        - an active row already holding the identifier means the current user
          must merge into it: that path carries memory-side effects and is
          forwarded to the monolith instead of executed here.

        The identifier lookup runs under the transaction-scoped advisory lock,
        so "lookup -> decide -> write" stays serialized; the lock is released
        by ending the transaction before any forward.
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
                # Merge hit: nothing has been written in this transaction, so
                # roll back to release the advisory lock before forwarding —
                # the monolith re-acquires it for the full merge.
                self.db.rollback()
                return IdentityOutcome(forward=True)

        current_end_user.identity_features = clean_features
        current_end_user.identity_status = identity_status
        self.db.commit()
        self.db.refresh(current_end_user)
        return IdentityOutcome(
            forward=False,
            final_id=current_end_user.id,
            identity_status=identity_status,
            merged=False,
        )

    def update_end_user_info(
        self, end_user_id: str, raw_update_data: dict[str, Any]
    ) -> InfoUpdateOutcome:
        """Update the end-user info record (PG side only).

        Returns the forward order when the (filtered) alias list differs from
        the stored one: the alias sync into the memory storage happens on the
        monolith. The caller has already resolved ``end_user_id`` to the merge
        target.
        """

        try:
            user_uuid = UUID(end_user_id)
        except ValueError:
            return InfoUpdateOutcome(error="无效的终端用户ID格式")

        record = self.info_repo.get_end_user_info(user_uuid)
        if not record:
            return InfoUpdateOutcome(error="终端用户信息记录不存在")

        update_data = dict(raw_update_data)
        # Placeholder names are dropped before the change comparison, exactly
        # like the host: a placeholder-only payload never triggers the sync.
        if (
            "other_name" in update_data
            and update_data["other_name"]
            and update_data["other_name"].strip() in _USER_PLACEHOLDER_NAMES
        ):
            logger.warning(
                "Rejecting placeholder other_name: %s", update_data["other_name"]
            )
            del update_data["other_name"]

        if "aliases" in update_data and update_data["aliases"]:
            update_data["aliases"] = [
                alias
                for alias in update_data["aliases"]
                if isinstance(alias, str)
                and alias.strip()
                and alias.strip() not in _USER_PLACEHOLDER_NAMES
            ]

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
                "meta_data": record.meta_data,
                "created_at": to_timestamp_ms(record.created_at),
                "updated_at": to_timestamp_ms(record.updated_at),
            },
        )


__all__ = ["EndUserService", "IdentityOutcome", "InfoUpdateOutcome"]
