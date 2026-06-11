"""
Metadata extractor utilities.

Provides helper functions for identifying user entities from post-dedup
graph data. The actual LLM extraction logic lives in MetadataExtractionStep.
"""

import logging
from typing import Dict, List

from app.core.memory.models.graph_models import ExtractedEntityNode

logger = logging.getLogger(__name__)

# 用户实体判定常量
USER_NAMES = {"用户", "我", "user", "i"}
CANONICAL_USER_TYPE = "用户"


def is_user_entity(entity: ExtractedEntityNode) -> bool:
    """判断实体是否为用户实体。"""
    name = (getattr(entity, "name", "") or "").strip().lower()
    etype = (getattr(entity, "entity_type", "") or "").strip()
    return name in USER_NAMES or etype == CANONICAL_USER_TYPE


def collect_user_entities_for_metadata(
    entity_nodes: List[ExtractedEntityNode],
) -> List[Dict]:
    """从去重后的实体列表中筛选用户实体，构造元数据提取的输入。

    将每个用户实体的 description 按分号拆分为列表，
    作为 Celery 异步元数据提取任务的输入。

    Args:
        entity_nodes: 去重后的实体节点列表

    Returns:
        用户实体字典列表，每项包含 entity_id、entity_name、descriptions
    """
    user_entities = []
    for entity in entity_nodes:
        if not is_user_entity(entity):
            continue

        desc = (getattr(entity, "description", "") or "").strip()
        if not desc:
            continue

        # 将分号分隔的 description 拆分为列表
        descriptions = [
            d.strip() for d in desc.replace("；", ";").split(";")
            if d.strip()
        ]
        if descriptions:
            user_entities.append({
                "entity_id": entity.id,
                "entity_name": entity.name,
                "descriptions": descriptions,
                "end_user_id": entity.end_user_id,
            })

    if user_entities:
        logger.info(
            f"收集到 {len(user_entities)} 个用户实体用于元数据提取"
        )
    else:
        logger.debug("未找到用户实体，跳过元数据提取")

    return user_entities
