"""
Neo4j 记录到实体模型的转换工具函数。

保留 _row_to_entity 供反思引擎 entity_similarity.py 使用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from app.core.utils.datetime_utils import utcnow_naive
from app.core.memory.models.graph_models import (
    EntityEntityEdge,
    ExtractedEntityNode,
    StatementEntityEdge,
)
from app.core.memory.models.graph_models import ExtractedEntityNode
from app.core.memory.utils.data.ontology import get_type_id


def _parse_dt(val: Any) -> datetime:
    """将任意类型的输入值解析为 datetime 对象"""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val)
        except Exception:
            pass
    # Fallback: now; upstream should provide real times
    return utcnow_naive()


def _row_to_entity(row: Dict[str, Any]) -> ExtractedEntityNode:
    """
    将 Neo4j 返回的数据库记录转换为 ExtractedEntityNode 模型对象

    Args:
        row: Neo4j 查询返回的记录字典

    Returns:
        ExtractedEntityNode: 实体节点对象
    """
    return ExtractedEntityNode(
        id=row.get("id"),
        name=row.get("name") or "",
        end_user_id=row.get("end_user_id") or "",
        user_id=row.get("user_id") or "",
        apply_id=row.get("apply_id") or "",
        created_at=_parse_dt(row.get("created_at")),
        entity_idx=int(row.get("entity_idx") or 0),
        statement_id=row.get("statement_id") or "",
        entity_type=row.get("entity_type") or "",
        type_id=row.get("type_id") or get_type_id(row.get("entity_type") or ""),
        description=row.get("description") or "",
        aliases=row.get("aliases") or [],
        name_embedding=row.get("name_embedding") or [],
        connect_strength=row.get("connect_strength") or "",
        is_explicit_memory=bool(row.get("is_explicit_memory", False)),
    )
