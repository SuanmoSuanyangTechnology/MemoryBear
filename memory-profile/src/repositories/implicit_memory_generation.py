"""SceneSummary and MemorySummary access for implicit-memory generation."""

from datetime import datetime
from typing import Any

from src.infrastructure.neo4j import Neo4jManager


class ImplicitMemorySummaryRepository:
    """Reads both summary types used to build an end user's implicit profile."""

    def __init__(self, connector: Neo4jManager):
        self.connector = connector

    async def count_by_end_user_id(self, end_user_id: str) -> int:
        """Count non-empty summaries without fetching their content."""
        results = await self.connector.read_query(
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
        results = await self.connector.read_query(query, **params)
        return [
            {**dict(result["scene"]), "summary_type": result["summary_type"]} for result in results
        ]


class ImplicitProfileGenerationRepository:
    """Persist the same API-owned profile row using the profile session manager."""

    def __init__(self, postgres):
        self.postgres = postgres

    async def config_source(self, config_id):
        from sqlalchemy import select

        from src.models.references import MemoryConfigRow, Workspace, WorkspaceDefaultModelPreset

        columns = [
            MemoryConfigRow.config_id,
            Workspace.id.label("workspace_id"),
            Workspace.tenant_id,
            Workspace.is_default_config,
            WorkspaceDefaultModelPreset.id.label("preset_id"),
        ]
        for slot in ("embedding", "llm", "rerank", "vision", "audio", "video"):
            columns.append(getattr(Workspace, slot).label(f"workspace_{slot}"))
            columns.append(
                getattr(WorkspaceDefaultModelPreset, f"{slot}_model_config_id").label(
                    f"preset_{slot}"
                )
            )
        statement = (
            select(*columns)
            .select_from(MemoryConfigRow)
            .outerjoin(Workspace, Workspace.id == MemoryConfigRow.workspace_id)
            .outerjoin(
                WorkspaceDefaultModelPreset,
                WorkspaceDefaultModelPreset.singleton_key == "default",
            )
            .where(MemoryConfigRow.config_id == config_id)
            .limit(1)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def save_profile(self, end_user_id: str, profile_data: dict) -> None:
        from uuid import uuid4

        from sqlalchemy import select

        from src.common.timing import utcnow_naive
        from src.models.references.implicit_emotions_storage_model import ImplicitEmotionsStorage

        async with self.postgres.session() as session:
            try:
                storage = (
                    await session.execute(
                        select(ImplicitEmotionsStorage).where(
                            ImplicitEmotionsStorage.end_user_id == end_user_id
                        )
                    )
                ).scalar_one_or_none()
                if storage is None:
                    storage = ImplicitEmotionsStorage(
                        id=uuid4(),
                        end_user_id=end_user_id,
                        created_at=utcnow_naive(),
                        updated_at=utcnow_naive(),
                    )
                    session.add(storage)
                    await session.flush()
                    await session.refresh(storage)
                storage.implicit_profile = profile_data
                storage.implicit_generated_at = utcnow_naive()
                storage.updated_at = utcnow_naive()
                await session.flush()
                await session.refresh(storage)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
