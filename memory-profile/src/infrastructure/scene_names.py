"""Scene-name → ``scene_type_id`` resolution for the merge (open build).

The API's merge asks the enterprise ``memory.ontology_query`` plugin. The
community build does not ship that plugin, but the API-owned ``ontology_scene``
table is readable here, so the same workspace-scoped name → id map is available
without the premium package. An empty table (or a failed read) degrades to
``{}`` — no normalization — which is exactly what the plugin-less API does.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable

import sqlalchemy as sa

from src.infrastructure.postgres import PostgresManager
from src.models.references import OntologySceneRow

logger = logging.getLogger(__name__)

SceneNameResolver = Callable[[uuid.UUID, list[str]], Awaitable[dict[str, str]]]


async def resolve_scene_name_ids(
    postgres: PostgresManager,
    workspace_id: uuid.UUID,
    scene_names: Iterable[str],
) -> dict[str, str]:
    """Return ``{scene_name: scene_type_id}`` for one workspace."""

    names = [str(name) for name in scene_names if name]
    if not names:
        return {}
    try:
        async with postgres.session() as session:
            rows = (
                await session.execute(
                    sa.select(
                        OntologySceneRow.scene_name,
                        OntologySceneRow.scene_id,
                    ).where(
                        OntologySceneRow.workspace_id == workspace_id,
                        OntologySceneRow.scene_name.in_(names),
                    )
                )
            ).all()
    except Exception:
        logger.warning(
            "[merge_end_users] scene name lookup failed; keeping stored keys",
            exc_info=True,
        )
        return {}
    return {
        str(scene_name): str(scene_id)
        for scene_name, scene_id in rows
        if scene_name and scene_id
    }


def build_scene_name_resolver(postgres: PostgresManager) -> SceneNameResolver:
    async def _resolve(
        workspace_id: uuid.UUID, scene_names: list[str]
    ) -> dict[str, str]:
        return await resolve_scene_name_ids(postgres, workspace_id, scene_names)

    return _resolve


__all__ = [
    "SceneNameResolver",
    "build_scene_name_resolver",
    "resolve_scene_name_ids",
]