"""Data access for the optional enterprise scene-type tables."""

from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


class OntologySceneTypeRepository:
    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def extraction_tables_available(self) -> bool:
        return bool(self.db.execute(text("""
            SELECT to_regclass('app_ontology_binding') IS NOT NULL
               AND to_regclass('ontology') IS NOT NULL
               AND to_regclass('ontology_scene_type') IS NOT NULL
               AND to_regclass('ontology_profile_field') IS NOT NULL
        """)).scalar())

    def get_names_by_ids(self, scene_ids: Sequence[UUID]) -> dict[str, str]:
        if not scene_ids:
            return {}
        stmt = text("""
            SELECT scene_type_id, scene_name
            FROM ontology_scene_type
            WHERE scene_type_id IN :scene_ids
        """).bindparams(bindparam("scene_ids", expanding=True))
        rows = self.db.execute(stmt, {"scene_ids": list(scene_ids)}).all()
        return {str(row.scene_type_id): row.scene_name for row in rows}

    async def get_ids_by_names_async(
        self, workspace_id: UUID, scene_names: Sequence[str],
    ) -> dict[str, str]:
        if not scene_names:
            return {}
        stmt = text("""
            SELECT st.scene_name, st.scene_type_id
            FROM ontology_scene_type AS st
            JOIN workspaces AS w ON w.tenant_id = st.tenant_id
            WHERE w.id = :workspace_id AND st.scene_name IN :scene_names
        """).bindparams(bindparam("scene_names", expanding=True))
        rows = (await self.db.execute(stmt, {
            "workspace_id": workspace_id, "scene_names": list(scene_names),
        })).all()
        return {row.scene_name: str(row.scene_type_id) for row in rows}

    def get_profile_fields_for_user(self, end_user_id: str) -> Sequence[Row[Any]]:
        from app.models import Conversation

        stmt_apps = select(Conversation.app_id).where(
            Conversation.user_id == end_user_id,
        ).distinct()
        app_ids = [row[0] for row in self.db.execute(stmt_apps).all() if row[0]]
        if not app_ids:
            return []
        stmt = text("""
            SELECT st.scene_name, st.scene_type_id, st.scene_description,
                   pf.field_key, pf.field_label, pf.field_definition,
                   pf.value_format, pf.include_examples, pf.exclude_examples
            FROM app_ontology_binding AS b
            JOIN ontology AS o ON b.ontology_id = o.ontology_id
            JOIN ontology_scene_type AS st ON o.scene_type_id = st.scene_type_id
            JOIN ontology_profile_field AS pf ON st.scene_type_id = pf.scene_type_id
            WHERE b.app_id IN :app_ids
              AND st.is_system_default IS FALSE
              AND pf.is_system_default IS FALSE
            ORDER BY st.scene_name, pf.created_at, pf.field_key
        """).bindparams(bindparam("app_ids", expanding=True))
        return self.db.execute(stmt, {"app_ids": app_ids}).all()
