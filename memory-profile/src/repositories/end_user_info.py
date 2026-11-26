"""Read-only PostgreSQL access for the migrated end-user endpoints.

Covers the queries the API's controllers/repositories performed:

* end-user resolution with the API's merge routing
  (``EndUserRepository.get_end_user_by_id_async``: active row first, otherwise the
  newest merge edge's active target);
* the ``end_user_info`` record read (``EndUserInfoRepository.get_end_user_info_async``);
* the workspace-scoped, filtered, paged listing behind ``GET /end_user/mapping``
  (``EndUserRepository.get_filtered_by_workspace``).

Read-only by construction: only ``select`` expressions are issued, so this file
is listed with the other read-only PostgreSQL repositories in
``scripts/check_import_boundaries.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, EndUserInfo, EndUserMerge


class EndUserInfoReadRepository:
    """End-user identity and info-record lookups used by the read/update handlers."""

    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def get_active_or_merged_end_user(
        self, end_user_id: UUID
    ) -> dict[str, Any] | None:
        """Return ``{"id", "workspace_id"}`` for an active user or its merge target.

        API-equivalent to ``EndUserRepository.get_end_user_by_id_async``: an
        inactive (merged-away) id routes to the newest merge target, and an
        inactive target is not returned.
        """

        async with self.postgres.session() as session:
            direct = await session.execute(
                select(EndUser.id, EndUser.workspace_id)
                .where(EndUser.id == end_user_id, EndUser.is_active.is_(True))
                .limit(1)
            )
            row = direct.first()
            if row is not None:
                return {"id": row[0], "workspace_id": row[1]}

            merged = await session.execute(
                select(EndUser.id, EndUser.workspace_id)
                .select_from(EndUserMerge)
                .join(EndUser, EndUser.id == EndUserMerge.target_id)
                .where(
                    EndUserMerge.origin_id == end_user_id,
                    EndUser.is_active.is_(True),
                )
                .order_by(EndUserMerge.id.desc())
                .limit(1)
            )
            row = merged.first()
            if row is None:
                return None
            return {"id": row[0], "workspace_id": row[1]}

    async def get_info(self, end_user_id: UUID) -> dict[str, Any] | None:
        """Return the ``end_user_info`` row as a plain dict (detached-safe)."""

        async with self.postgres.session() as session:
            row = (
                await session.execute(
                    select(
                        EndUserInfo.id,
                        EndUserInfo.end_user_id,
                        EndUserInfo.other_name,
                        EndUserInfo.aliases,
                        EndUserInfo.meta_data,
                        EndUserInfo.created_at,
                        EndUserInfo.updated_at,
                    )
                    .where(EndUserInfo.end_user_id == end_user_id)
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            return {
                "id": row.id,
                "end_user_id": row.end_user_id,
                "other_name": row.other_name,
                "aliases": row.aliases,
                "meta_data": row.meta_data,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    async def get_display_row(self, end_user_id: UUID) -> dict[str, Any] | None:
        """The columns behind ``GET /end_user/info``'s display projection.

        Display reads ``meta_data_new`` only (a NULL/``{}`` value never falls
        back to the historical ``meta_data`` column), plus the two top-level
        fields the response echoes.
        """

        async with self.postgres.session() as session:
            row = (
                await session.execute(
                    select(
                        EndUserInfo.id,
                        EndUserInfo.end_user_id,
                        EndUserInfo.other_name,
                        EndUserInfo.aliases,
                        EndUserInfo.meta_data_new,
                        EndUserInfo.created_at,
                        EndUserInfo.updated_at,
                    )
                    .where(EndUserInfo.end_user_id == end_user_id)
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            return {
                "id": row.id,
                "end_user_id": row.end_user_id,
                "other_name": row.other_name,
                "aliases": row.aliases,
                "meta_data_new": row.meta_data_new,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    async def list_mapping(
        self,
        *,
        workspace_id: UUID,
        end_user_id: UUID | None,
        other_id: str | None,
        other_name: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Workspace-scoped end users for ``GET /end_user/mapping``.

        Matches the API's filters and ordering exactly: active users of the
        workspace, optional exact ``end_user_id`` / ``other_id`` and case-sensitive
        substring ``other_name`` filters combined with AND, newest ``created_at``
        first, plus the unfiltered-by-page total.
        """

        conditions = [
            EndUser.workspace_id == workspace_id,
            EndUser.is_active.is_(True),
        ]
        if end_user_id is not None:
            conditions.append(EndUser.id == end_user_id)
        if other_id is not None:
            conditions.append(EndUser.other_id == other_id)
        if other_name is not None:
            conditions.append(EndUser.other_name.ilike(f"%{other_name}%"))

        async with self.postgres.session() as session:
            total = await session.scalar(
                select(func.count()).select_from(EndUser).where(*conditions)
            )
            rows = (
                await session.execute(
                    select(EndUser.id, EndUser.other_id, EndUser.other_name)
                    .where(*conditions)
                    .order_by(EndUser.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        items = [
            {
                "end_user_id": str(row.id),
                "other_id": row.other_id or "",
                "other_name": row.other_name or "",
            }
            for row in rows
        ]
        return items, int(total or 0)