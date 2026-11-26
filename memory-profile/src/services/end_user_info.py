"""``POST /end_user/info/update``: the call's PostgreSQL and graph effects.

The application service now owns the request-level validation and the plain
column updates; what it forwarded to the monolith was exactly this call's
memory-side effect — keeping the graph's own user-entity node in step with the
alias list, plus the denormalised ``end_users.other_name``.

So this use case keeps the host's business rules rather than mirroring the whole
controller:

* a merged-away id resolves to its target before anything is written;
* only ``other_name`` / ``aliases`` are writable (``meta_data`` is maintained by
  the extraction pipeline, and writing the display projection back would wipe it);
* a blank or placeholder display name never overwrites the stored one;
* an alias change still writes when it clears the list — the API's update is
  destructive by design;
* the graph sync is best effort: PostgreSQL has already committed, so a failed
  graph write is logged and reported, never rolled back.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.common.timing import to_timestamp_ms
from src.repositories.end_user_aliases_mutation import (
    AliasUpdateResult,
    update_user_entity_aliases,
)
from src.repositories.end_user_info import EndUserInfoReadRepository
from src.repositories.end_user_info_mutation import EndUserInfoMutationRepository

logger = logging.getLogger(__name__)

#: The API's ``_USER_PLACEHOLDER_NAMES``; a display name never takes these.
USER_PLACEHOLDER_NAMES = frozenset({"用户", "我", "user", "i"})

#: Fields the call may write (the API's ``allowed_fields``).
WRITABLE_FIELDS = frozenset({"other_name", "aliases"})


def is_user_placeholder_name(name: str) -> bool:
    """Whether ``name`` is a user placeholder, ignoring case and padding."""

    return name.strip().lower() in USER_PLACEHOLDER_NAMES


def normalize_other_name(value: Any) -> str | None:
    """Return the display name to write, or ``None`` when it must be skipped."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if is_user_placeholder_name(normalized):
        logger.warning(
            "refusing to store the placeholder name %r as other_name", normalized
        )
        return None
    return normalized


def normalize_aliases(values: Any) -> list[str]:
    """Strip, drop blanks/placeholders and de-duplicate, keeping the first spelling."""

    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped or is_user_placeholder_name(stripped):
            continue
        lowered = stripped.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(stripped)
    return result


class EndUserInfoNotFoundError(Exception):
    """The end-user info record does not exist (the API's USER_NOT_FOUND branch)."""


class EndUserInfoInvalidIdError(Exception):
    """The end user id is malformed (the API's INVALID_USER_ID branch)."""


@dataclass(frozen=True, slots=True)
class AliasSyncReport:
    """What the graph side did, reported back for observability only."""

    status: str = "skipped"
    affected_count: int = 0
    error: str = ""


class EndUserInfoUpdateService:
    """Apply an info update and keep the graph's user entity in step."""

    def __init__(
        self,
        *,
        reads: EndUserInfoReadRepository,
        mutations: EndUserInfoMutationRepository,
        alias_writer: Any | None = None,
    ) -> None:
        self._reads = reads
        self._mutations = mutations
        #: ``update_user_entity_aliases`` bound to the graph client and outbox, or
        #: ``None`` in a deployment with no write path (the sync then reports
        #: ``skipped`` instead of pretending it ran).
        self._alias_writer = alias_writer

    async def update(
        self,
        *,
        workspace_id: UUID,
        end_user_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the update for one end user of ``workspace_id``.

        Raises :class:`EndUserInfoInvalidIdError` for a malformed id and
        :class:`EndUserInfoNotFoundError` when the info record is missing; the
        transport maps both onto the API's business codes.
        """

        try:
            parsed_id = uuid.UUID(str(end_user_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise EndUserInfoInvalidIdError(end_user_id) from exc

        resolved = await self._reads.get_active_or_merged_end_user(parsed_id)
        if resolved is None or str(resolved["workspace_id"]) != str(workspace_id):
            raise EndUserInfoNotFoundError(str(parsed_id))

        target_id: UUID = resolved["id"]
        current = await self._reads.get_display_row(target_id)
        if current is None:
            raise EndUserInfoNotFoundError(str(target_id))

        # ``meta_data`` is the extraction pipeline's column; nothing else writes it.
        update_data = {
            key: value
            for key, value in update_data.items()
            if key in WRITABLE_FIELDS
        }

        update_other_name = False
        other_name = None
        if "other_name" in update_data:
            other_name = normalize_other_name(update_data["other_name"])
            update_other_name = other_name is not None and other_name != current["other_name"]

        update_aliases = False
        aliases: list[str] | None = None
        if "aliases" in update_data:
            aliases = normalize_aliases(update_data["aliases"])
            update_aliases = aliases != list(current["aliases"] or [])

        row = await self._mutations.update_info(
            target_id,
            other_name=other_name,
            aliases=aliases,
            update_other_name=update_other_name,
            update_aliases=update_aliases,
        )
        if row is None:
            raise EndUserInfoNotFoundError(str(target_id))

        report = (
            await self._sync_aliases(str(target_id), aliases or [])
            if update_aliases
            else AliasSyncReport()
        )

        return {
            "end_user_info_id": str(row["id"]),
            "end_user_id": str(row["end_user_id"]),
            "other_name": row["other_name"],
            "aliases": row["aliases"],
            "meta_data": row["meta_data"] if isinstance(row["meta_data"], dict) else {},
            "created_at": to_timestamp_ms(row["created_at"]),
            "updated_at": to_timestamp_ms(row["updated_at"]),
            "neo4j_sync_status": report.status,
        }

    async def _sync_aliases(self, end_user_id: str, aliases: list[str]) -> AliasSyncReport:
        if self._alias_writer is None:
            logger.info(
                "[end_user_info] alias graph sync is not configured; end_user=%s",
                end_user_id,
            )
            return AliasSyncReport(status="unavailable")
        try:
            result: AliasUpdateResult = await self._alias_writer(end_user_id, aliases)
        except Exception as error:
            logger.error(
                "[end_user_info] alias graph sync failed: end_user=%s error=%s",
                end_user_id,
                error,
                exc_info=True,
            )
            return AliasSyncReport(status="failed", error=str(error))
        return AliasSyncReport(
            status="success", affected_count=result.affected_count
        )


__all__ = [
    "USER_PLACEHOLDER_NAMES",
    "WRITABLE_FIELDS",
    "AliasSyncReport",
    "EndUserInfoInvalidIdError",
    "EndUserInfoNotFoundError",
    "EndUserInfoUpdateService",
    "is_user_placeholder_name",
    "normalize_aliases",
    "normalize_other_name",
    "update_user_entity_aliases",
]
