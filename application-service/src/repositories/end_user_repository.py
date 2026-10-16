"""End-user table read/write paths (frozen port of the identity-side subset of
the monolith ``end_user_repository``).

The memory-side half of a merge (Neo4j reassignment, ``memory_*`` reference
migration, reflection dispatch) is NOT ported: those writes stay behind the
cross-domain forward to the monolith's memory API until the memory service
contract is frozen.
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.end_user_info_model import EndUserInfo
from ..models.end_user_model import EndUser, EndUserMerge
from ..utils.datetime_utils import utcnow_naive


class EndUserRepository:
    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def get_end_user_by_id(self, end_user_id: UUID | str) -> EndUser | None:
        """Active end user by id, else route through the merge map.

        The id argument is passed to the query untouched (host parity: a
        malformed string fails at the database layer, not here).
        """

        end_user = (
            self.db.query(EndUser)
            .filter(EndUser.id == end_user_id, EndUser.is_active.is_(True))
            .first()
        )
        if end_user:
            return end_user
        return self.resolve_merge_by_origin_id(end_user_id)

    def resolve_merge_by_origin_id(self, origin_id: UUID | str) -> EndUser | None:
        """Merge target for a soft-deleted origin id (host: latest mapping row,
        no workspace filter on either hop)."""

        target_id = (
            self.db.query(EndUserMerge.target_id)
            .filter(EndUserMerge.origin_id == origin_id)
            .order_by(EndUserMerge.id.desc())
            .limit(1)
            .scalar()
        )
        if not target_id:
            return None
        return (
            self.db.query(EndUser)
            .filter(EndUser.id == target_id, EndUser.is_active.is_(True))
            .limit(1)
            .first()
        )

    def get_end_user_by_other_id(self, workspace_id: UUID, other_id: str) -> EndUser | None:
        """Active end user by (workspace, other_id), else route through the
        merge map; None when neither resolves."""

        end_user = (
            self.db.query(EndUser)
            .filter(
                EndUser.workspace_id == workspace_id,
                EndUser.other_id == other_id,
                EndUser.is_active.is_(True),
            )
            .order_by(EndUser.created_at.asc())
            .first()
        )
        if end_user:
            return end_user
        return self.resolve_merge_by_other_id(workspace_id, other_id)

    def resolve_merge_by_other_id(self, workspace_id: UUID, other_id: str) -> EndUser | None:
        """Merge target for a soft-deleted origin ``other_id`` within the
        workspace (host: latest mapping row, target must be active + in-workspace)."""

        merge_record = (
            self.db.query(EndUserMerge)
            .filter(
                EndUserMerge.workspace_id == workspace_id,
                EndUserMerge.origin_other_id == other_id,
            )
            .order_by(EndUserMerge.id.desc())
            .first()
        )
        if not merge_record:
            return None
        return (
            self.db.query(EndUser)
            .filter(
                EndUser.id == merge_record.target_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
            )
            .first()
        )

    async def get_end_user_by_other_id_async(
        self, workspace_id: UUID, other_id: str
    ) -> EndUser | None:
        """Async variant used on the runtime plane; ``self.db`` must be an
        AsyncSession."""

        result = await self.db.execute(
            select(EndUser)
            .filter(
                EndUser.workspace_id == workspace_id,
                EndUser.other_id == other_id,
                EndUser.is_active.is_(True),
            )
            .order_by(EndUser.created_at.asc())
        )
        end_user = result.scalars().first()
        if end_user:
            return end_user
        return await self.resolve_merge_by_other_id_async(workspace_id, other_id)

    async def resolve_merge_by_other_id_async(
        self, workspace_id: UUID, other_id: str
    ) -> EndUser | None:
        """Async variant of ``resolve_merge_by_other_id`` (same selection:
        latest mapping row, target active + in-workspace)."""

        merge_result = await self.db.execute(
            select(EndUserMerge.target_id)
            .filter(
                EndUserMerge.workspace_id == workspace_id,
                EndUserMerge.origin_other_id == other_id,
            )
            .order_by(EndUserMerge.id.desc())
            .limit(1)
        )
        target_id = merge_result.scalars().first()
        if not target_id:
            return None
        target_result = await self.db.execute(
            select(EndUser)
            .filter(
                EndUser.id == target_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
            )
            .limit(1)
        )
        return target_result.scalars().first()

    def get_filtered_by_workspace(
        self,
        workspace_id: UUID,
        end_user_id: UUID | None = None,
        other_id: str | None = None,
        other_name: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[EndUser], int]:
        """Filtered + paginated active end users of one workspace (AND filters,
        newest first); returns (rows, total)."""

        base_query = self.db.query(EndUser).filter(
            EndUser.workspace_id == workspace_id,
            EndUser.is_active.is_(True),
        )
        if end_user_id is not None:
            base_query = base_query.filter(EndUser.id == end_user_id)
        if other_id is not None:
            base_query = base_query.filter(EndUser.other_id == other_id)
        if other_name is not None:
            base_query = base_query.filter(EndUser.other_name.ilike(f"%{other_name}%"))

        total = base_query.count()
        if limit is not None:
            end_users = (
                base_query.order_by(EndUser.created_at.desc()).offset(offset).limit(limit).all()
            )
        else:
            end_users = base_query.all()
        return end_users, total

    def acquire_identity_lock(self, workspace_id: UUID, identity_features: str) -> None:
        """Transaction-scoped exclusive lock on (workspace, identity_features).

        Serializes the find -> decide -> write sequence so concurrent requests
        carrying the same identifier cannot both conclude "no match" and leave
        two confirmed rows behind. Released automatically at commit/rollback.
        """

        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"eu_identity:{workspace_id}|{identity_features}"},
        )

    def find_active_by_identity_features(
        self,
        workspace_id: UUID,
        identity_features: str,
        *,
        for_update: bool = False,
    ) -> EndUser | None:
        """Oldest active row holding the identifier (host ordering); the row
        lock is the second guard during a merge (the advisory lock is released
        by the first inner commit)."""

        stmt = (
            select(EndUser)
            .where(
                EndUser.workspace_id == workspace_id,
                EndUser.identity_features == identity_features,
                EndUser.is_active.is_(True),
            )
            .order_by(EndUser.created_at.asc())
            .limit(1)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalars().first()

    def _sync_end_user_info(self, end_user: EndUser, other_name: str | None) -> None:
        """Sync ``end_user.other_name`` and the EndUserInfo row (no commit).

        - non-empty name: update ``end_user.other_name`` and upsert EndUserInfo
        - empty name: keep the stored name; create the EndUserInfo row when
          missing, matching the new-user branch that always creates one

        A single INSERT ... ON CONFLICT replaces the select-then-write pair and
        relies on the unique index on ``end_user_info.end_user_id`` to prevent
        duplicate rows under concurrency.
        """

        normalized_name = other_name.strip() if other_name else ""
        if normalized_name:
            end_user.other_name = normalized_name

        now = utcnow_naive()
        stmt = pg_insert(EndUserInfo).values(
            end_user_id=end_user.id,
            other_name=normalized_name or end_user.other_name or "",
            aliases=[],
            meta_data={},
            created_at=now,
            updated_at=now,
        )
        if normalized_name:
            # ON CONFLICT DO UPDATE does not fire Column.onupdate, so updated_at
            # is set explicitly; an unchanged name skips the write entirely.
            stmt = stmt.on_conflict_do_update(
                index_elements=[EndUserInfo.end_user_id],
                set_={"other_name": normalized_name, "updated_at": now},
                where=EndUserInfo.other_name.is_distinct_from(normalized_name),
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=[EndUserInfo.end_user_id])
        self.db.execute(stmt)

    def get_or_create_with_config(
        self,
        app_id: UUID | None,
        workspace_id: UUID,
        other_id: str,
        memory_config_id: UUID | None = None,
        other_name: str | None = None,
    ) -> EndUser:
        """Get (or create) the workspace end user for ``other_id`` and feed the
        memory config in; a merged origin ``other_id`` routes to its target.
        ``other_name`` is synced onto an existing/merged user in the same
        commit; a new end user gets an ``EndUserInfo`` row carrying it.
        """

        end_user = (
            self.db.query(EndUser)
            .filter(
                EndUser.workspace_id == workspace_id,
                EndUser.other_id == other_id,
                EndUser.is_active.is_(True),
            )
            .order_by(EndUser.created_at.asc())
            .first()
        )
        if end_user:
            if app_id is not None:
                end_user.app_id = app_id
            if memory_config_id and not end_user.memory_config_id:
                end_user.memory_config_id = memory_config_id
            self._sync_end_user_info(end_user, other_name)
            self.db.commit()
            self.db.refresh(end_user)
            return end_user

        merged_target = self.resolve_merge_by_other_id(workspace_id, other_id)
        if merged_target:
            if app_id is not None:
                merged_target.app_id = app_id
            if memory_config_id and not merged_target.memory_config_id:
                merged_target.memory_config_id = memory_config_id
            self._sync_end_user_info(merged_target, other_name)
            self.db.commit()
            self.db.refresh(merged_target)
            return merged_target

        normalized_name = other_name.strip() if other_name and other_name.strip() else ""
        end_user = EndUser(
            app_id=app_id,
            workspace_id=workspace_id,
            other_id=other_id,
            other_name=normalized_name,
            memory_config_id=memory_config_id,
        )
        self.db.add(end_user)
        self.db.flush()

        self.db.add(
            EndUserInfo(
                end_user_id=end_user.id,
                other_name=normalized_name,
                aliases=[],
                meta_data={},
            )
        )
        self.db.commit()
        self.db.refresh(end_user)
        return end_user

    @contextmanager
    def _acquire_eu_lock(self, workspace_id: UUID, other_id: str):
        """Transaction-scoped exclusive lock on (workspace, other_id).

        Serializes the find -> decide -> write sequence so concurrent requests
        cannot both conclude "no match" and leave duplicate rows behind.
        The key matches the host repository so both services exclude each other
        while they run side by side; released automatically at commit/rollback.
        """

        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{workspace_id}|{other_id}"},
        )
        yield

    def get_or_create_end_user(
        self,
        app_id: UUID,
        workspace_id: UUID,
        other_id: str,
        original_user_id: str | None = None,
        other_name: str | None = None,
    ) -> EndUser:
        """Get or create the workspace end user for ``other_id`` (flag dropped)."""

        end_user, _ = self.get_or_create_end_user_with_status(
            app_id=app_id,
            workspace_id=workspace_id,
            other_id=other_id,
            original_user_id=original_user_id,
            other_name=other_name,
        )
        return end_user

    def get_or_create_end_user_with_status(
        self,
        app_id: UUID,
        workspace_id: UUID,
        other_id: str,
        original_user_id: str | None = None,
        other_name: str | None = None,
    ) -> tuple[EndUser, bool]:
        """Get or create the workspace end user; the flag reports whether this
        call created the row. The decision is made inside the advisory lock so
        callers never act on a stale pre-lock probe. A merged origin ``other_id``
        routes to its target; a new user gets an empty ``EndUserInfo`` row in
        the same commit."""

        try:
            with self._acquire_eu_lock(workspace_id, other_id):
                end_user = (
                    self.db.query(EndUser)
                    .filter(
                        EndUser.workspace_id == workspace_id,
                        EndUser.other_id == other_id,
                        EndUser.is_active.is_(True),
                    )
                    .order_by(EndUser.created_at.asc())
                    .first()
                )
                if end_user:
                    end_user.app_id = app_id
                    self.db.commit()
                    self.db.refresh(end_user)
                    return end_user, False

                merged_target = self.resolve_merge_by_other_id(workspace_id, other_id)
                if merged_target:
                    merged_target.app_id = app_id
                    self.db.commit()
                    self.db.refresh(merged_target)
                    return merged_target, False

                end_user = EndUser(
                    app_id=app_id,
                    workspace_id=workspace_id,
                    other_id=other_id,
                )
                self.db.add(end_user)
                self.db.flush()

                self.db.add(
                    EndUserInfo(
                        end_user_id=end_user.id,
                        other_name=other_name or "",
                        aliases=[],
                        meta_data={},
                    )
                )
                self.db.commit()
            self.db.refresh(end_user)
            return end_user, True
        except Exception:
            self.db.rollback()
            raise

    async def get_or_create_end_user_async(
        self,
        app_id: UUID,
        workspace_id: UUID,
        other_id: str,
        original_user_id: str | None = None,
        other_name: str | None = None,
    ) -> EndUser:
        """Async variant used on the runtime plane; ``self.db`` must be an
        AsyncSession. No advisory lock (host parity); merge routing comes from
        the async lookup, and a new user gets an empty ``EndUserInfo`` row."""

        try:
            end_user = await self.get_end_user_by_other_id_async(workspace_id, other_id)
            if end_user:
                end_user.app_id = app_id
                await self.db.commit()
                await self.db.refresh(end_user)
                return end_user

            end_user = EndUser(
                app_id=app_id,
                workspace_id=workspace_id,
                other_id=other_id,
            )
            self.db.add(end_user)
            await self.db.flush()

            self.db.add(
                EndUserInfo(
                    end_user_id=end_user.id,
                    other_name=other_name or "",
                    aliases=[],
                    meta_data={},
                )
            )
            await self.db.commit()
            await self.db.refresh(end_user)
            return end_user
        except Exception:
            await self.db.rollback()
            raise


__all__ = ["EndUserRepository"]
