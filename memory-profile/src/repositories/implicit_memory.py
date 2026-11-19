"""Profile read/write repository for implicit memory profiles."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.common.normalization import parse_uuid
from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser
from src.models.references.implicit_emotions_storage_model import ImplicitEmotionsStorage
from src.repositories.end_user import owned_user_select


logger = logging.getLogger(__name__)


class ImplicitMemoryRepository:
    def __init__(self, postgres: PostgresManager, neo4j=None) -> None:
        self.postgres = postgres
        self.neo4j = neo4j

    async def get_profile(self, end_user_id: str | UUID, workspace_id: UUID) -> Any:
        user_uuid = parse_uuid(end_user_id)
        if user_uuid is None:
            return None
        statement = (
            select(ImplicitEmotionsStorage.implicit_profile)
            .where(
                ImplicitEmotionsStorage.end_user_id == str(end_user_id),
                self._scope(user_uuid, workspace_id),
            )
            .order_by(
                ImplicitEmotionsStorage.updated_at.desc().nulls_last(),
                ImplicitEmotionsStorage.id.desc(),
            )
            .limit(1)
        )
        async with self.postgres.session() as session:
            profile = await session.scalar(statement)
        return profile

    @staticmethod
    def _scope(end_user_id: UUID, workspace_id: UUID):
        return owned_user_select(
            EndUser.id, end_user_id=end_user_id, workspace_id=workspace_id
        ).exists()

    async def count_by_end_user_id(self, end_user_id: str) -> int:
        """Count non-empty summaries without fetching their content."""
        results = await self.neo4j.read_query(
            """
            CALL {
                MATCH (scene:SceneSummary {end_user_id: $end_user_id})
                WHERE trim(coalesce(scene.content, '')) <> ''
                RETURN count(scene) AS summary_count
                UNION ALL
                MATCH (scene:MemorySummary {end_user_id: $end_user_id})
                WHERE scene.delete_at IS NULL
                  AND trim(coalesce(scene.content, '')) <> ''
                RETURN count(scene) AS summary_count
            }
            RETURN sum(summary_count) AS count
            """,
            end_user_id=end_user_id,
        )
        return int(results[0]["count"]) if results else 0

    async def find_by_end_user_id(
        self,
        end_user_id: str,
        limit: int = 1000,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return both non-empty summary types in a single limited result set."""
        query = """
        CALL {
            MATCH (scene:SceneSummary {end_user_id: $end_user_id})
            RETURN scene
            UNION ALL
            MATCH (scene:MemorySummary {end_user_id: $end_user_id})
            WHERE scene.delete_at IS NULL
            RETURN scene
        }
        WITH scene
        WHERE trim(coalesce(scene.content, '')) <> ''
        """
        params: dict[str, Any] = {"end_user_id": end_user_id, "limit": limit}

        if start_date:
            query += " AND coalesce(scene.ended_at, scene.created_at) >= $start_date"
            params["start_date"] = start_date

        if end_date:
            query += " AND coalesce(scene.ended_at, scene.created_at) <= $end_date"
            params["end_date"] = end_date

        query += """
        RETURN properties(scene) AS scene,
               CASE WHEN scene:SceneSummary THEN 'scene_summary'
                    ELSE 'memory_summary' END AS summary_type
        ORDER BY coalesce(scene.ended_at, scene.created_at) DESC, scene.id ASC
        LIMIT $limit
        """
        results = await self.neo4j.read_query(query, **params)
        return [
            {**dict(result["scene"]), "summary_type": result["summary_type"]} for result in results
        ]

    async def save_profile(self, end_user_id, workspace_id, profile):
        from uuid import uuid4

        from src.common.datetime_utils import utcnow_naive

        async with self.postgres.session() as session:
            if not await session.scalar(select(self._scope(UUID(end_user_id), workspace_id))):
                raise ValueError("User not found in workspace")

            stmt = select(ImplicitEmotionsStorage).where(
                ImplicitEmotionsStorage.end_user_id == end_user_id
            )
            storage = (await session.execute(stmt)).scalar_one_or_none()
            if storage is None:
                storage = ImplicitEmotionsStorage(
                    id=uuid4(),
                    end_user_id=end_user_id,
                    created_at=utcnow_naive(),
                    updated_at=utcnow_naive()
                )
                session.add(storage)
                await session.flush()
                await session.refresh(storage)
                logger.info(f"创建用户存储记录成功: end_user_id={end_user_id}")

            storage.implicit_profile = profile
            storage.implicit_generated_at = utcnow_naive()
            storage.updated_at = utcnow_naive()

            await session.flush()
            await session.refresh(storage)
            logger.info(f"更新隐性记忆画像成功: end_user_id={end_user_id}")
            await session.commit()
