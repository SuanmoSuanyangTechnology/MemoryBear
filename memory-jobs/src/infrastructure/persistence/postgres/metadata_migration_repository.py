"""PostgreSQL adapter for one-time legacy metadata migration."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.domain.profile.read_models import (
    MetadataMigrationCandidate,
    MetadataMigrationCursor,
    MetadataMigrationScanCandidate,
)
from src.domain.shared.ids import EndUserId, WorkspaceId
from src.infrastructure.persistence.postgres.mappers import to_metadata_migration_candidate
from src.models import EndUser, EndUserInfo, Workspace


class SqlAlchemyMetadataMigrationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def migration_candidates(
        self,
        *,
        cutoff: datetime,
        after: MetadataMigrationCursor | None,
        limit: int,
        excluded_workspaces: frozenset[WorkspaceId] = frozenset(),
    ) -> tuple[list[MetadataMigrationScanCandidate], MetadataMigrationCursor | None]:
        statement = (
            select(
                EndUser.id,
                EndUser.workspace_id,
                EndUser.write_time,
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .join(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
                sa.or_(EndUser.created_at < cutoff, EndUser.created_at.is_(None)),
                EndUserInfo.meta_data_new.is_(None),
            )
            .order_by(EndUser.write_time.desc().nulls_last(), EndUser.id)
            .limit(limit)
        )
        if excluded_workspaces:
            statement = statement.where(
                EndUser.workspace_id.not_in(sorted(item.value for item in excluded_workspaces))
            )
        if after is not None:
            later_id = EndUser.id > after.end_user_id.value
            if after.write_time is None:
                statement = statement.where(EndUser.write_time.is_(None), later_id)
            else:
                statement = statement.where(
                    sa.or_(
                        EndUser.write_time < after.write_time,
                        sa.and_(EndUser.write_time == after.write_time, later_id),
                        EndUser.write_time.is_(None),
                    )
                )
        candidates = [
            MetadataMigrationScanCandidate(
                EndUserId(user_id), WorkspaceId(workspace_id), write_time
            )
            for user_id, workspace_id, write_time in self._session.execute(statement)
        ]
        next_cursor = (
            MetadataMigrationCursor(candidates[-1].write_time, candidates[-1].end_user_id)
            if candidates and len(candidates) == limit
            else None
        )
        return candidates, next_cursor

    def remaining_count(self, *, cutoff: datetime) -> int:
        """仅统计有 info 行的待迁移用户，与候选分页保持一致。"""
        statement = (
            select(sa.func.count(EndUser.id))
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .join(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
                sa.or_(EndUser.created_at < cutoff, EndUser.created_at.is_(None)),
                EndUserInfo.meta_data_new.is_(None),
            )
        )
        return int(self._session.scalar(statement) or 0)

    def get_migration_candidate(
        self, end_user_id: EndUserId, workspace_id: WorkspaceId
    ) -> MetadataMigrationCandidate | None:
        statement = (
            select(
                EndUser.id.label("end_user_id"),
                EndUser.workspace_id,
                EndUser.created_at,
                EndUserInfo.id.label("metadata_id"),
                EndUserInfo.meta_data,
                EndUserInfo.meta_data_new,
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                EndUser.id == end_user_id.value,
                EndUser.workspace_id == workspace_id.value,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
            )
            .limit(1)
        )
        row = self._session.execute(statement).mappings().one_or_none()
        return None if row is None else to_metadata_migration_candidate(row)

    def cas_write_metadata_new(
        self,
        *,
        end_user_id: EndUserId,
        original_meta_data: object,
        merged_metadata: dict[str, object],
    ) -> bool:
        statement = text(
            """
            UPDATE end_user_info
            SET meta_data_new = CAST(:merged AS jsonb), updated_at = NOW()
            WHERE end_user_id = :end_user_id
              AND meta_data_new IS NULL
              AND meta_data IS NOT DISTINCT FROM CAST(:original AS jsonb)
              AND EXISTS (
                SELECT 1 FROM end_users AS eu JOIN workspaces AS ws ON ws.id = eu.workspace_id
                WHERE eu.id = end_user_info.end_user_id
                  AND eu.is_active IS TRUE AND ws.is_active IS TRUE
              )
            RETURNING id
            """
        )
        row = self._session.execute(
            statement,
            {
                "merged": _json(merged_metadata),
                "original": _json_or_null(original_meta_data),
                "end_user_id": end_user_id.value,
            },
        ).first()
        if row is not None:
            self._session.execute(
                text("""
            UPDATE end_users SET memory_tags = NULL, memory_tags_updated_at = NULL,
                memory_tags_source_fingerprint = NULL,
                memory_insight = NULL, key_findings = NULL,
                behavior_pattern = NULL, growth_trajectory = NULL,
                memory_insight_updated_at = NULL,
                user_summary = NULL, personality_traits = NULL, core_values = NULL,
                one_sentence_summary = NULL, user_summary_updated_at = NULL
            WHERE id = :user_id AND is_active IS TRUE
"""),
                {"user_id": end_user_id.value},
            )
        return row is not None


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_or_null(value: object) -> str | None:
    return None if value is None else _json(value)
