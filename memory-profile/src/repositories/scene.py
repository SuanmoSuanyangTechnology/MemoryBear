from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from src.infrastructure.postgres import PostgresManager
from src.models.references import OntologySceneRow


class SceneRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def list_visible_simple_scenes(self, workspace_id: UUID) -> list[dict[str, object]]:
        statement = (
            select(
                OntologySceneRow.scene_id,
                OntologySceneRow.scene_name,
            )
            .where(OntologySceneRow.workspace_id == workspace_id)
            .order_by(
                OntologySceneRow.scene_name.asc(),
                OntologySceneRow.scene_id.asc(),
            )
        )
        async with self.postgres.session() as session:
            rows = (await session.execute(statement)).mappings().all()
            return [dict(row) for row in rows]
