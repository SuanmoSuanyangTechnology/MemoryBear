"""把存储的 scene_type_id 解析为场景名，供模型与接口展示使用，不修改存储。"""

import asyncio
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory.models.metadata_models import is_scene_type_id, split_metadata_entry
from app.db import get_db_context
from app.plugins import get_plugin

logger = logging.getLogger(__name__)

END_USER_DOMAIN_VALUES_LIMIT = 10


def load_scene_names(metadata: object) -> dict[str, str]:
    domains = metadata.get("domain_specific_info") if isinstance(metadata, dict) else None
    if not isinstance(domains, dict):
        return {}
    ids: dict[str, UUID] = {}
    for key in domains:
        if is_scene_type_id(key):
            ids[key] = UUID(key)
    if not ids:
        return {}
    plugin = get_plugin("memory.ontology_query")
    if plugin is None:
        return {}
    try:
        with get_db_context() as db:
            names = plugin.get_names_by_ids(db, list(set(ids.values())))
        return {key: names[str(value)] for key, value in ids.items() if str(value) in names}
    except Exception:
        logger.warning("[Metadata] 场景名查询失败，保留存储原键", exc_info=True)
        return {}


async def load_scene_names_async(metadata: object) -> dict[str, str]:
    # 在 worker 线程内创建并关闭同步 Session，避免在事件循环里持有同步连接。
    return await asyncio.to_thread(load_scene_names, metadata)


async def project_end_user_domains_async(
    metadata: object, db: AsyncSession, workspace_id: UUID,
) -> dict[str, dict[str, dict]]:
    """接口展示投影：租户内 ID 转名称，未知 ID 不外泄，查询失败向上传播。

    现有插件的 ID 查询不带租户范围，故批量反查名称并核对 ID 后才允许输出。
    使用调用方的异步会话，不改动存储，也不影响模型侧原有的容错读取策略。
    """
    domains = metadata.get("domain_specific_info") if isinstance(metadata, dict) else None
    if not isinstance(domains, dict):
        return {}

    valid_domains: dict[str, list[str]] = {}
    for key, items in domains.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(items, list):
            logger.warning("[EndUserInfo] 忽略格式异常的领域分组")
            continue
        valid_domains[key] = [item for item in items if isinstance(item, str)]

    ids = {key: UUID(key) for key in valid_domains if is_scene_type_id(key)}
    names: dict[str, str] = {}
    plugin = get_plugin("memory.ontology_query") if valid_domains else None
    labels = {}
    if plugin is not None:
        # run_sync 将同步 SQLAlchemy 插件适配到当前 AsyncSession。
        candidates = await db.run_sync(
            plugin.get_names_by_ids, list(dict.fromkeys(ids.values())),
        )
        canonical_ids = {str(value) for value in ids.values()}
        candidates = {
            key: name for key, name in candidates.items()
            if key in canonical_ids
            and isinstance(name, str) and name.strip() and not is_scene_type_id(name)
        }
        legacy_names = [key for key in valid_domains if key not in ids]
        if candidates or legacy_names:
            scoped_ids = await plugin.get_ids_by_names_async(
                db, workspace_id, list(dict.fromkeys([*candidates.values(), *legacy_names])),
            )
            names = {
                key: candidates[str(value)] for key, value in ids.items()
                if str(value) in candidates
                and str(scoped_ids.get(candidates[str(value)])) == str(value)
            }
            scene_ids = list(dict.fromkeys(UUID(str(value)) for value in scoped_ids.values()))
            labels = await plugin.get_profile_field_labels_async(db, workspace_id, scene_ids)
        else:
            scoped_ids = {}

    result: dict[str, dict[str, dict]] = {}
    merged = set()
    for key, items in valid_domains.items():
        if key in ids and key not in names:
            continue
        destination = names.get(key, key)
        fields = result.setdefault(destination, {})
        scene_id = str(scoped_ids.get(destination, "")) if plugin is not None else ""
        for item in items:
            entry = split_metadata_entry(item)
            if not entry or not entry[0] or entry[1]:
                continue
            field_key, _, content = entry
            label = labels.get((scene_id, field_key))
            field = fields.setdefault(field_key, {
                "field_label": label if isinstance(label, str) and label.strip() else field_key,
                "values": [],
            })
            field["values"].append(content)
        if destination in merged:
            for field in fields.values():
                field["values"] = list(dict.fromkeys(field["values"]))
        merged.add(destination)
    for fields in result.values():
        for field in fields.values():
            field["values"] = field["values"][:END_USER_DOMAIN_VALUES_LIMIT]
    return result
