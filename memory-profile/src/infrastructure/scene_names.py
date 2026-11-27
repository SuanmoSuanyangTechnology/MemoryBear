"""Scene-name → id resolution for the merge (API parity).

The monolith's merge asks the enterprise ``memory.ontology_query`` plugin, which
resolves names against the tenant-scoped ``ontology_scene_type`` layer. That
layer is an enterprise capability — the community build ships neither the plugin
nor the table — so this resolver asks memory-enterprise over HTTP, through the
same client the profile's read path already uses for the reverse (id → name)
lookup.

When the service is absent (community build) or the call fails, the lookup
degrades to the API-owned legacy ``ontology_scene`` table, and finally to ``{}``
(no normalization), which is what the plugin-less API does. The enterprise table
itself is never read here: it is not part of this service's database contract.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import sqlalchemy as sa

from src.infrastructure.postgres import PostgresManager
from src.models.references import OntologySceneRow, Workspace

logger = logging.getLogger(__name__)

SceneNameResolver = Callable[[uuid.UUID, list[str]], Awaitable[dict[str, str]]]


async def _tenant_of(
    postgres: PostgresManager, workspace_id: uuid.UUID
) -> uuid.UUID | None:
    """The workspace's tenant, which the enterprise lookup is scoped by."""

    async with postgres.session() as session:
        return await session.scalar(
            sa.select(Workspace.tenant_id).where(Workspace.id == workspace_id)
        )


async def _enterprise_names(
    postgres: PostgresManager,
    enterprise: Any,
    workspace_id: uuid.UUID,
    names: list[str],
) -> dict[str, str] | None:
    """Tenant-scoped name → id via memory-enterprise, or ``None`` when unusable."""

    if enterprise is None or not getattr(enterprise, "enabled", False):
        return None
    try:
        tenant_id = await _tenant_of(postgres, workspace_id)
        if tenant_id is None:
            return None
        return dict(
            await enterprise.scene_ids(tenant_id=tenant_id, scene_names=names)
        )
    except Exception:
        logger.warning(
            "[merge_end_users] enterprise scene-id lookup failed; falling back "
            "to the read-only ontology_scene table",
            exc_info=True,
        )
        return None


async def _legacy_names(
    postgres: PostgresManager,
    workspace_id: uuid.UUID,
    names: list[str],
) -> dict[str, str]:
    """Workspace-scoped lookup over the API-owned ``ontology_scene`` table."""

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
    return {
        str(scene_name): str(scene_id)
        for scene_name, scene_id in rows
        if scene_name and scene_id
    }


async def resolve_scene_name_ids(
    postgres: PostgresManager,
    enterprise: Any | None,
    workspace_id: uuid.UUID,
    scene_names: Iterable[str],
) -> dict[str, str]:
    """Return ``{scene_name: scene_type_id}`` for one workspace."""

    names = [str(name) for name in scene_names if name]
    if not names:
        return {}
    mapped = await _enterprise_names(postgres, enterprise, workspace_id, names)
    if mapped is not None:
        # A successful empty answer is authoritative (the enterprise layer owns
        # the canonical map); only an unusable service falls back.
        return mapped
    try:
        return await _legacy_names(postgres, workspace_id, names)
    except Exception:
        logger.warning(
            "[merge_end_users] scene name lookup failed; keeping stored keys",
            exc_info=True,
        )
        return {}


def build_scene_name_resolver(
    postgres: PostgresManager, enterprise: Any | None = None
) -> SceneNameResolver:
    async def _resolve(
        workspace_id: uuid.UUID, scene_names: list[str]
    ) -> dict[str, str]:
        return await resolve_scene_name_ids(
            postgres, enterprise, workspace_id, scene_names
        )

    return _resolve


__all__ = [
    "SceneNameResolver",
    "build_scene_name_resolver",
    "resolve_scene_name_ids",
]
