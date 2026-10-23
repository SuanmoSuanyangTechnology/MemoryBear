"""把存储的 scene_type_id 解析为场景名，供模型侧使用，不修改存储。"""

import asyncio
import logging
from uuid import UUID

from app.core.memory.models.metadata_models import is_scene_type_id
from app.db import get_db_context
from app.plugins import get_plugin

logger = logging.getLogger(__name__)


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
