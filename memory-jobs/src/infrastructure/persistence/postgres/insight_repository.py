"""SQLAlchemy implementation of the memory-insight aggregate.

The conditional update keeps the API's conditions: the row is written only when
the source it was generated from did not move. The comparison is done in SQL so
the check and the write are one atomic statement; when it matches no row, the
adapter re-reads the source and lets the aggregate classify the miss
(``MemoryInsight.classify_missed_write``), so "superseded" versus "gone" stays
a domain decision.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.profile.model import MemoryInsight, WriteOutcome
from src.domain.profile.read_models import InsightSource
from src.domain.shared.ids import EndUserId, WorkspaceId
from src.infrastructure.persistence.postgres.conditions import (
    active_user_in_workspace,
    active_workspace_exists,
)
from src.infrastructure.persistence.postgres.mappers import to_insight_source
from src.models import EndUser, EndUserInfo, Workspace


class SqlAlchemyMemoryInsightRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_source(
        self, end_user_id: EndUserId, workspace_id: WorkspaceId
    ) -> InsightSource | None:
        statement = (
            select(
                EndUser.id.label("end_user_id"),
                EndUser.workspace_id,
                EndUserInfo.id.label("metadata_id"),
                EndUserInfo.meta_data.label("meta_data"),
                EndUserInfo.updated_at.label("metadata_updated_at"),
                EndUser.write_time,
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                *active_user_in_workspace(
                    end_user_id=end_user_id.value, workspace_id=workspace_id.value
                ),
                sa.or_(
                    Workspace.storage_type.is_(None),
                    Workspace.storage_type == "neo4j",
                ),
            )
            .limit(1)
        )
        row = self._session.execute(statement).mappings().one_or_none()
        return None if row is None else to_insight_source(row)

    def save_if_source_unchanged(self, insight: MemoryInsight) -> WriteOutcome:
        """Write the insight back only when the source did not move meanwhile.

        The guard is part of the ``UPDATE`` (one statement, no read-then-write
        race). A missed update costs one re-read so the aggregate, not the
        adapter, decides whether the row moved or disappeared.
        """

        version = insight.source_version
        workspace_active = active_workspace_exists(
            insight.workspace_id.value,
            sa.or_(Workspace.storage_type.is_(None), Workspace.storage_type == "neo4j"),
        )
        if version.metadata_row_exists:
            metadata_unchanged = sa.exists().where(
                EndUserInfo.end_user_id == insight.end_user_id.value,
                EndUserInfo.updated_at.is_not_distinct_from(
                    version.metadata_updated_at
                ),
            )
        else:
            metadata_unchanged = ~sa.exists().where(
                EndUserInfo.end_user_id == insight.end_user_id.value
            )

        result = self._session.execute(
            sa.update(EndUser)
            .where(
                EndUser.id == insight.end_user_id.value,
                EndUser.workspace_id == insight.workspace_id.value,
                EndUser.is_active.is_(True),
                EndUser.write_time.is_not_distinct_from(version.write_time),
                workspace_active,
                metadata_unchanged,
            )
            .values(
                memory_insight=insight.content.overview,
                memory_insight_updated_at=insight.generated_at,
                key_findings=json.dumps(
                    [
                        {
                            "finding": finding.finding,
                            "recommended_action": finding.recommended_action,
                        }
                        for finding in insight.content.findings
                    ],
                    ensure_ascii=False,
                ),
                behavior_pattern="",
                growth_trajectory="",
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            return WriteOutcome.UPDATED

        current = self.find_source(insight.end_user_id, insight.workspace_id)
        return insight.classify_missed_write(
            None if current is None else current.version
        )
