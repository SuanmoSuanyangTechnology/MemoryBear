"""Request-local profile projection; shared caches always contain full profiles."""

from copy import deepcopy
import logging

from app.core.memory.models.metadata_models import split_metadata_entry

logger = logging.getLogger(__name__)


def _filter_entries(value, allowed_keys: set[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        parsed = split_metadata_entry(item)
        if parsed is None:
            continue
        field_key, level3, _ = parsed
        if allowed_keys is not None and (field_key not in allowed_keys or level3):
            continue
        result.append(item)
    return result


def project_metadata(record: dict, scope: dict | str | None) -> dict:
    result = deepcopy(record) if isinstance(record, dict) else {}
    for key in ("identity_background", "relationships"):
        value = result.get(key)
        result[key] = [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []

    raw_scene_type_id = scope.get("scene_type_id") if isinstance(scope, dict) else scope
    scene_type_id = str(raw_scene_type_id) if raw_scene_type_id else None
    raw_field_keys = scope.get("field_keys") if isinstance(scope, dict) else None
    field_keys = set(raw_field_keys) if raw_field_keys is not None else None
    domains = result.get("domain_specific_info")
    value = domains.get(scene_type_id) if isinstance(domains, dict) and scene_type_id else None
    filtered = _filter_entries(value, field_keys) if field_keys is not None else (
        [item for item in value if isinstance(item, str) and item.strip()]
        if isinstance(value, list) else []
    )
    result["domain_specific_info"] = {scene_type_id: filtered} if scene_type_id and filtered else {}
    return result


async def scoped_metadata(ctx, record: dict) -> dict:
    from app.plugins import get_plugin

    scope = None
    resolver = get_plugin("memory.metadata_scope")
    if ctx.app_id is None:
        logger.warning("Metadata application context missing; using default_only")
    elif resolver is not None:
        # Query failures propagate: never fall back to the full profile.
        scope = await resolver.resolve(ctx.app_id, ctx.end_user_id)
    logger.info("Metadata scope=%s", "bounded" if scope else "default_only")
    return project_metadata(record, scope)
