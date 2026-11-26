"""Write access for ``POST /end_user/info/update``.

Single-table updates owned by the end-user-info domain:

* ``end_user_info.other_name`` / ``aliases`` (only the provided fields), plus
  ``updated_at``, in one transaction;
* the denormalised ``end_users.other_name`` (and its ``updated_at``) when the
  display name actually changed — the API performed that second update inside the
  same business call.

DDL stays with the API's Alembic chain; this repository only issues DML for the
narrow ``info/update`` use case. The API's best-effort Neo4j alias sync is not
performed here (no graph write path in this service) — see the migration report.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.common.timing import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, EndUserInfo


class EndUserInfoMutationRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def update_info(
        self,
        end_user_id: UUID,
        *,
        other_name: str | None = None,
        aliases: list[str] | None = None,
        update_other_name: bool = False,
        update_aliases: bool = False,
    ) -> dict[str, Any] | None:
        """Apply the API's info update and return the refreshed row as a dict.

        ``update_other_name`` / ``update_aliases`` carry the API's field-presence
        semantics: an explicitly provided (even empty) alias list clears the
        column, while an omitted field is untouched. Returns ``None`` when the
        info record does not exist.
        """

        async with self.postgres.session() as session:
            info = (
                await session.execute(
                    select(EndUserInfo).where(
                        EndUserInfo.end_user_id == end_user_id
                    ).limit(1)
                )
            ).scalars().first()
            if info is None:
                return None

            now = utcnow_naive()
            if update_other_name:
                info.other_name = other_name
            if update_aliases:
                info.aliases = aliases
            info.updated_at = now

            if update_other_name:
                end_user = (
                    await session.execute(
                        select(EndUser).where(EndUser.id == end_user_id).limit(1)
                    )
                ).scalars().first()
                if end_user is not None:
                    end_user.other_name = other_name
                    end_user.updated_at = now

            await session.commit()
            await session.refresh(info)

            return {
                "id": info.id,
                "end_user_id": info.end_user_id,
                "other_name": info.other_name,
                "aliases": info.aliases,
                "meta_data": info.meta_data,
                "created_at": info.created_at,
                "updated_at": info.updated_at,
            }