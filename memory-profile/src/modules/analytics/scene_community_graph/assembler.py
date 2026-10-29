"""Pure grouping logic: no network, authentication, cache, or database writes."""

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from src.modules.analytics.scene_community_graph.schemas import (
    TIME_FIELDS,
    CommunityGroup,
    CommunityProperties,
    GraphData,
    Scene,
    SceneProperties,
    Statistics,
    UnassignedGroup,
    validate_business_id,
)
from src.modules.analytics.scene_community_graph.types import bad_data

COMMUNITY_FIELDS = tuple(CommunityProperties.model_fields)
SCENE_FIELDS = tuple(SceneProperties.model_fields)


def _time(value: Any) -> int | None:
    if value is None:
        return None
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, int) and not isinstance(value, bool):
        # The response model checks the 13-digit millisecond range.
        return value
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("invalid datetime")
    # SceneStorage stores UTC wall time as a Neo4j localdatetime.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    # Integer arithmetic avoids float rounding and truncates sub-millisecond precision.
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _project(raw: Any, fields: tuple[str, ...]) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("properties must be an object")
    result = {field: raw.get(field) for field in fields}
    for field in TIME_FIELDS:
        result[field] = _time(result[field])
    return result


def _id(row: dict, seen: set[str]) -> str:
    value = row.get("id")
    if not isinstance(value, str):
        raise ValueError("missing ID")
    validate_business_id(value)
    if value in seen:
        raise ValueError("duplicate ID")
    seen.add(value)
    return value


def _sort_key(properties, names: tuple[str, ...], identifier: str) -> tuple:
    value = next(
        (getattr(properties, name) for name in names if getattr(properties, name) is not None), None
    )
    if value is None:
        return (True, 0, identifier)
    return (False, -value, identifier)


def assemble(community_rows: list[dict], scene_rows: list[dict]) -> GraphData:
    try:
        return _assemble(community_rows, scene_rows)
    except (ValidationError, ValueError, TypeError, AttributeError, KeyError, OverflowError) as exc:
        raise bad_data() from exc


def _assemble(community_rows: list[dict], scene_rows: list[dict]) -> GraphData:
    groups: dict[str, CommunityGroup] = {}
    fallback_count: set[str] = set()
    community_ids: set[str] = set()
    for row in community_rows:
        identifier = _id(row, community_ids)
        props = _project(row.get("properties"), COMMUNITY_FIELDS)
        if props["member_count"] is None:
            fallback_count.add(identifier)
            props["member_count"] = 0
        groups[identifier] = CommunityGroup(
            id=identifier, properties=CommunityProperties(**props), scene_count=0, scenes=[]
        )

    unassigned: list[Scene] = []
    scene_ids: set[str] = set()
    for row in scene_rows:
        identifier = _id(row, scene_ids)
        props = _project(row.get("properties"), SCENE_FIELDS)
        raw_parent = props["scene_community_id"]
        if raw_parent is None:
            parent, reason = None, "NO_PARENT"
        elif not isinstance(raw_parent, str):
            parent, reason = None, "INVALID_PARENT_ID"
        else:
            parent = raw_parent.strip() or None
            reason = "NO_PARENT" if parent is None else None
        props["scene_community_id"] = parent
        if parent is not None and parent in groups:
            scene = Scene(id=identifier, properties=SceneProperties(**props))
            groups[parent].scenes.append(scene)
        else:
            scene = Scene(
                id=identifier,
                properties=SceneProperties(**props),
                unassigned_reason=reason or "PARENT_NOT_FOUND",
            )
            unassigned.append(scene)

    def scene_key(scene):
        return _sort_key(scene.properties, ("ended_at", "started_at", "created_at"), scene.id)

    for group in groups.values():
        group.scenes.sort(key=scene_key)
        group.scene_count = len(group.scenes)
        if group.id in fallback_count:
            group.properties.member_count = group.scene_count
    result = sorted(
        groups.values(),
        key=lambda g: (
            not bool(g.scenes),
            *_sort_key(g.properties, ("updated_at", "created_at"), g.id),
        ),
    )
    if unassigned:
        unassigned.sort(key=scene_key)
        result.append(
            UnassignedGroup(
                properties=CommunityProperties(member_count=len(unassigned)),
                scene_count=len(unassigned),
                scenes=unassigned,
            )
        )
    assigned = len(scene_ids) - len(unassigned)
    return GraphData(
        groups=result,
        statistics=Statistics(
            total_scene_communities=len(groups),
            total_scene_summaries=len(scene_ids),
            assigned_scene_summaries=assigned,
            unassigned_scene_summaries=len(unassigned),
            total_groups=len(result),
        ),
    )
