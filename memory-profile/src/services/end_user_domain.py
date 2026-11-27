"""Display projection of an end user's ``domain_specific_info``.

The API assembles this in ``ontology_services/scene_name_resolver.py::
project_end_user_domains_async`` for ``GET /end_user/info``: it turns the stored
scene-type ids into scene names, drops ids the caller's workspace cannot see, and
groups the level-2 entries by scene and field for the UI.

Scene names resolve through memory-enterprise (the host's
``memory.ontology_query`` plugin equivalent) and fall back to the read-only
``ontology_scene`` table, which keeps a deployment without the enterprise
service displaying stored keys instead of losing the section.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from src.core.domain.profile import normalize_new_metadata, split_metadata_entry
from src.infrastructure.postgres import PostgresManager
from src.models.references import OntologySceneRow

logger = logging.getLogger(__name__)

#: Host ``END_USER_DOMAIN_VALUES_LIMIT``: values shown per field.
DOMAIN_VALUES_LIMIT = 10

#: ``(tenant_id, scene_ids) -> {scene_id: scene_name}``.
SceneNameLookup = Callable[[UUID, list[str]], Awaitable[dict[str, str]]]
#: ``scene_ids -> {scene_id: {field_key: label}}``.
FieldLabelLookup = Callable[[list[str]], Awaitable[dict[str, dict[str, str]]]]


def is_scene_type_id(key: object) -> bool:
    """Whether a ``domain_specific_info`` key is a scene type uuid."""

    if not isinstance(key, str):
        return False
    try:
        UUID(key)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def lookup_scene_names_from_table(
    postgres: PostgresManager,
    workspace_id: UUID,
    scene_ids: list[str],
) -> dict[str, str]:
    """Read-only fallback: ``ontology_scene`` rows of this workspace."""

    if not scene_ids:
        return {}
    try:
        async with postgres.session() as session:
            rows = (
                await session.execute(
                    sa.select(
                        OntologySceneRow.scene_id, OntologySceneRow.scene_name
                    ).where(
                        OntologySceneRow.scene_id.in_(
                            [UUID(value) for value in scene_ids]
                        ),
                        OntologySceneRow.workspace_id == workspace_id,
                    )
                )
            ).all()
    except Exception:
        logger.warning(
            "[end_user_domain] scene-name table lookup failed; keeping stored keys",
            exc_info=True,
        )
        return {}
    return {
        str(scene_id): str(scene_name)
        for scene_id, scene_name in rows
        if scene_id and scene_name
    }


async def resolve_scene_names(
    record_meta: Any,
    *,
    postgres: PostgresManager,
    workspace_id: UUID,
    enterprise_lookup: SceneNameLookup | None = None,
) -> dict[str, str]:
    """``{scene_id: scene_name}`` for the ids this record references."""

    scene_ids = scene_ids_of(record_meta)
    if not scene_ids:
        return {}

    if enterprise_lookup is not None:
        try:
            resolved = await enterprise_lookup(workspace_id, scene_ids)
            if resolved:
                return {str(key): str(value) for key, value in resolved.items()}
        except Exception:
            logger.warning(
                "[end_user_domain] enterprise scene-name lookup failed; "
                "falling back to the read-only table",
                exc_info=True,
            )
    return await lookup_scene_names_from_table(postgres, workspace_id, scene_ids)


def scene_ids_of(record_meta: Any) -> list[str]:
    """The distinct scene type ids a record's ``domain_specific_info`` references."""

    domains = normalize_new_metadata(record_meta)["domain_specific_info"]
    return list(dict.fromkeys(key for key in domains if is_scene_type_id(key)))


async def resolve_field_labels(
    record_meta: Any,
    *,
    enterprise_lookup: FieldLabelLookup | None = None,
) -> dict[str, dict[str, str]]:
    """``{scene_id: {field_key: label}}`` for the record's scenes.

    Field labels exist only in the enterprise profile-field table, so a
    deployment without that service returns nothing and the projection falls
    back to the field keys.
    """

    scene_ids = scene_ids_of(record_meta)
    if not scene_ids or enterprise_lookup is None:
        return {}
    try:
        return await enterprise_lookup(scene_ids)
    except Exception:
        logger.warning(
            "[end_user_domain] enterprise field-label lookup failed; "
            "falling back to field keys",
            exc_info=True,
        )
        return {}


def project_domains(
    record_meta: Any,
    scene_names: dict[str, str] | None = None,
    field_labels: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Group entries by scene and field: ``{scene: {field_key: {label, values}}}``.

    Host parity (``project_end_user_domains_async``):

    * a uuid key that resolved to no name is dropped, so a deleted or foreign
      scene never leaks into the response;
    * a non-uuid key passes through unchanged (legacy pre-id storage);
    * only level-2 entries (no level-3 part) are grouped;
    * same-named scenes and fields merge with duplicate values removed;
    * every field keeps at most :data:`DOMAIN_VALUES_LIMIT` values.

    ``field_labels`` is ``{scene_id: {field_key: label}}`` from the enterprise
    profile-field table. A field with no label — no enterprise service, a legacy
    key, or a field the table does not describe — falls back to its field key,
    which is what the API does in the same situation.
    """

    domains = normalize_new_metadata(record_meta)["domain_specific_info"]
    names = scene_names or {}
    labels = field_labels or {}

    result: dict[str, dict[str, dict[str, Any]]] = {}
    #: The scene id each destination came from, so labels resolve per id even
    #: after two same-named scenes merged into one destination.
    destination_ids: dict[str, str] = {}
    merged: set[str] = set()
    for key, items in domains.items():
        if not isinstance(items, list):
            continue
        if is_scene_type_id(key) and key not in names:
            continue
        destination = names.get(key, key)
        destination_ids.setdefault(destination, key if is_scene_type_id(key) else "")
        fields = result.setdefault(destination, {})
        scene_id = destination_ids[destination]
        for item in items:
            entry = split_metadata_entry(item)
            if not entry or not entry[0] or entry[1]:
                continue
            field_key, _level3, content = entry
            label = labels.get(scene_id, {}).get(field_key)
            field = fields.setdefault(
                field_key,
                {
                    "field_label": (
                        label if isinstance(label, str) and label.strip() else field_key
                    ),
                    "values": [],
                },
            )
            field["values"].append(content)
        if destination in merged:
            for field in fields.values():
                field["values"] = list(dict.fromkeys(field["values"]))
        merged.add(destination)

    for fields in result.values():
        for field in fields.values():
            field["values"] = field["values"][:DOMAIN_VALUES_LIMIT]
    return result


__all__ = [
    "DOMAIN_VALUES_LIMIT",
    "SceneNameLookup",
    "is_scene_type_id",
    "lookup_scene_names_from_table",
    "project_domains",
    "resolve_field_labels",
    "resolve_scene_names",
    "scene_ids_of",
]
