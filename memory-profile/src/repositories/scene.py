from __future__ import annotations

from uuid import UUID

from sqlalchemy import UUID as SQLAlchemyUUID
from sqlalchemy import Boolean, String, and_, case, column, or_, select, table

from src.infrastructure.postgres import PostgresManager

_ontology_scene_type = table(
    "ontology_scene_type",
    column("scene_type_id", SQLAlchemyUUID(as_uuid=True)),
    column("tenant_id", SQLAlchemyUUID(as_uuid=True)),
    column("scene_name", String),
    column("is_system_default", Boolean),
)


class SceneRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def list_visible_simple_scenes(self, tenant_id: UUID) -> list[dict[str, object]]:
        visible = or_(
            and_(
                _ontology_scene_type.c.tenant_id.is_(None),
                _ontology_scene_type.c.is_system_default.is_(True),
            ),
            and_(
                _ontology_scene_type.c.tenant_id == tenant_id,
                _ontology_scene_type.c.is_system_default.is_(False),
            ),
        )
        statement = (
            select(
                _ontology_scene_type.c.scene_type_id,
                _ontology_scene_type.c.scene_name,
            )
            .where(visible)
            .order_by(
                case((_ontology_scene_type.c.tenant_id.is_(None), 0), else_=1),
                _ontology_scene_type.c.scene_name.asc(),
                _ontology_scene_type.c.scene_type_id.asc(),
            )
        )
        async with self.postgres.session() as session:
            rows = (await session.execute(statement)).mappings().all()
            return [dict(row) for row in rows]
