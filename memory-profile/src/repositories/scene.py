from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, case, or_, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import OntologySceneType


class SceneRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def list_visible_simple_scenes(self, tenant_id: UUID) -> list[dict[str, object]]:
        visible = or_(
            and_(
                OntologySceneType.tenant_id.is_(None),
                OntologySceneType.is_system_default.is_(True),
            ),
            and_(
                OntologySceneType.tenant_id == tenant_id,
                OntologySceneType.is_system_default.is_(False),
            ),
        )
        statement = (
            select(
                OntologySceneType.scene_type_id,
                OntologySceneType.scene_name,
            )
            .where(visible)
            .order_by(
                case((OntologySceneType.tenant_id.is_(None), 0), else_=1),
                OntologySceneType.scene_name.asc(),
                OntologySceneType.scene_type_id.asc(),
            )
        )
        async with self.postgres.session() as session:
            rows = (await session.execute(statement)).mappings().all()
            return [dict(row) for row in rows]
