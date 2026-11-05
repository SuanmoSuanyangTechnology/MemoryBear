"""偏好类型页面服务

偏好类型页只读：列表 / 详情均读 `ontology_preference_scene` +
`ontology_preference_class`，进入时惰性种入；同时提供本体接口做展示名投影。
"""

from __future__ import annotations

import logging
import uuid
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.preference_type_initializer import ensure_seeded_async
from app.models.ontology_preference import (
    OntologyPreferenceClass,
    OntologyPreferenceScene,
)

logger = logging.getLogger(__name__)


def pick_localized(zh: Optional[str], en: Optional[str], language: str) -> str:
    """按语言取文案：缺失回退另一语言，再回退空串。"""
    if language == "en":
        return en or zh or ""
    return zh or en or ""


async def _count_items(
    db: AsyncSession, preference_scene_id: uuid.UUID, item_type: str
) -> int:
    total = await db.scalar(
        select(func.count())
        .select_from(OntologyPreferenceClass)
        .where(
            OntologyPreferenceClass.preference_scene_id == preference_scene_id,
            OntologyPreferenceClass.item_type == item_type,
        )
    )
    return int(total or 0)


async def _get_scene(
    db: AsyncSession, workspace_id: uuid.UUID, domain: str
) -> Optional[OntologyPreferenceScene]:
    result = await db.execute(
        select(OntologyPreferenceScene).where(
            OntologyPreferenceScene.workspace_id == workspace_id,
            OntologyPreferenceScene.domain == domain,
        )
    )
    return result.scalars().first()


async def list_preference_types(
    db: AsyncSession, workspace_id: uuid.UUID, language: str
) -> dict:
    """6.2 偏好类型列表：无分页、无搜索、无筛选。"""
    await ensure_seeded_async(db, workspace_id)
    result = await db.execute(
        select(OntologyPreferenceScene)
        .where(OntologyPreferenceScene.workspace_id == workspace_id)
        .order_by(OntologyPreferenceScene.created_at.asc())
    )
    scenes = result.scalars().all()

    items = []
    for scene in scenes:
        items.append(
            {
                "domain": scene.domain,
                "display_name": pick_localized(
                    scene.display_name_zh, scene.display_name_en, language
                )
                or scene.domain,
                "preference_count": await _count_items(db, scene.preference_scene_id, "subject"),
                "task_count": await _count_items(db, scene.preference_scene_id, "situation"),
                "readonly": True,
            }
        )
    return {"items": items, "total": len(items)}


async def get_preference_type(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    domain: str,
    language: str,
) -> Optional[dict]:
    """6.3 偏好类型详情；未知 domain 返回 None（由路由转 4000）。"""
    await ensure_seeded_async(db, workspace_id)
    scene = await _get_scene(db, workspace_id, domain)
    if scene is None:
        return None

    result = await db.execute(
        select(OntologyPreferenceClass)
        .where(OntologyPreferenceClass.preference_scene_id == scene.preference_scene_id)
        .order_by(
            OntologyPreferenceClass.item_type.asc(),
            OntologyPreferenceClass.sort_order.asc(),
        )
    )
    rows = result.scalars().all()

    categories = [
        {
            "key": row.item_key,
            "label": pick_localized(row.label_zh, row.label_en, language) or row.item_key,
            "description": pick_localized(
                row.display_description_zh, row.display_description_en, language
            ),
        }
        for row in rows
        if row.item_type == "subject"
    ]
    situations = [
        {
            "key": row.item_key,
            "label": pick_localized(row.label_zh, row.label_en, language) or row.item_key,
        }
        for row in rows
        if row.item_type == "situation"
    ]

    return {
        "domain": scene.domain,
        "display_name": pick_localized(
            scene.display_name_zh, scene.display_name_en, language
        )
        or scene.domain,
        "preference_count": len(categories),
        "task_count": len(situations),
        "readonly": True,
        "preference_categories": categories,
        "situations": situations,
        "note": pick_localized(scene.note_zh, scene.note_en, language),
    }


async def get_preference_display_names(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    domains: Iterable[Optional[str]],
    language: str,
) -> dict[str, str]:
    """批量取偏好域展示名（本体列表 / 组合摘要投影用）；不传空域。"""
    unique_domains = [item for item in dict.fromkeys(domains) if item]
    if not unique_domains:
        return {}
    result = await db.execute(
        select(
            OntologyPreferenceScene.domain,
            OntologyPreferenceScene.display_name_zh,
            OntologyPreferenceScene.display_name_en,
        ).where(
            OntologyPreferenceScene.workspace_id == workspace_id,
            OntologyPreferenceScene.domain.in_(unique_domains),
        )
    )
    return {
        domain: pick_localized(zh, en, language) or domain
        for domain, zh, en in result.all()
    }


async def preference_domain_exists(
    db: AsyncSession, workspace_id: uuid.UUID, domain: str
) -> bool:
    """本体绑定校验：当前工作空间下是否存在该偏好域（不存在则惰性种入后重查）。"""
    if await _get_scene(db, workspace_id, domain) is not None:
        return True
    await ensure_seeded_async(db, workspace_id)
    return await _get_scene(db, workspace_id, domain) is not None
