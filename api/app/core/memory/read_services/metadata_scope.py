"""Request-local profile projection; shared caches always contain full profiles."""

from copy import deepcopy
import logging

logger = logging.getLogger(__name__)


def _split_entry(item) -> tuple[str, str, str] | None:
    if not isinstance(item, str):
        return None
    parts = [part.strip() for part in item.strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    if len(parts) != 3 or not parts[2]:
        return None
    return parts[0], parts[1], parts[2]


def _filter_entries(value, allowed_keys: set[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        parsed = _split_entry(item)
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

    scene_name = scope.get("scene_name") if isinstance(scope, dict) else scope
    field_keys = set(scope.get("field_keys", ())) if isinstance(scope, dict) else None
    domains = result.get("domain_specific_info")
    value = domains.get(scene_name) if isinstance(domains, dict) and scene_name else None
    filtered = _filter_entries(value, field_keys) if field_keys is not None else (
        [item for item in value if isinstance(item, str) and item.strip()]
        if isinstance(value, list) else []
    )
    result["domain_specific_info"] = {scene_name: filtered} if scene_name and filtered else {} # TODO：PG的存储键从scene_name改为scene_type_id后，此处检索也需要改成scene_type_id
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
