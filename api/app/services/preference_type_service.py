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
from app.db import get_async_db_context
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


async def _ensure_seeded_in_new_session(
    workspace_id: uuid.UUID, domain: str = "coding"
) -> None:
    """用独立会话种入并提交，不污染请求/调用方事务。

    种子文件异常只记 error 日志，不阻断只读接口——已种入的 workspace 仍可正常返回。
    """
    try:
        async with get_async_db_context() as seed_db:
            await ensure_seeded_async(seed_db, workspace_id, domain=domain)
    except Exception:
        logger.error(
            "[PreferenceType] lazy seed failed workspace=%s domain=%s",
            workspace_id,
            domain,
            exc_info=True,
        )


async def list_preference_types(
    db: AsyncSession, workspace_id: uuid.UUID, language: str
) -> dict:
    """6.2 偏好类型列表：无分页、无搜索、无筛选。"""
    await _ensure_seeded_in_new_session(workspace_id)
    result = await db.execute(
        select(OntologyPreferenceScene)
        .where(OntologyPreferenceScene.workspace_id == workspace_id)
        .order_by(OntologyPreferenceScene.created_at.asc())
    )
    scenes = result.scalars().all()

    # 一次聚合取回所有域的分类/任务计数，避免逐域 N+1
    counts: dict[tuple[uuid.UUID, str], int] = {}
    if scenes:
        count_rows = await db.execute(
            select(
                OntologyPreferenceClass.preference_scene_id,
                OntologyPreferenceClass.item_type,
                func.count(),
            )
            .where(
                OntologyPreferenceClass.preference_scene_id.in_(
                    [scene.preference_scene_id for scene in scenes]
                )
            )
            .group_by(
                OntologyPreferenceClass.preference_scene_id,
                OntologyPreferenceClass.item_type,
            )
        )
        counts = {
            (scene_id, item_type): int(total)
            for scene_id, item_type, total in count_rows.all()
        }

    items = []
    for scene in scenes:
        items.append(
            {
                "domain": scene.domain,
                "display_name": pick_localized(
                    scene.display_name_zh, scene.display_name_en, language
                )
                or scene.domain,
                "preference_count": counts.get((scene.preference_scene_id, "subject"), 0),
                "task_count": counts.get((scene.preference_scene_id, "situation"), 0),
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
    """6.3 偏好类型详情；未知 domain 返回 None（由路由转 BizCode.NOT_FOUND，即 4000）。"""
    await _ensure_seeded_in_new_session(workspace_id)
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
    """批量取偏好域展示名（本体列表 / 组合摘要投影用）；不传空域。

    契约：调用方需先确保当前 workspace 已种入（列表、详情、组合摘要入口
    都会先调用 ensure_seeded*）；未种入时返回空字典。
    """
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
    """本体绑定校验：当前工作空间下是否存在该偏好域（不存在则惰性种入后重查）。

    种子在独立会话中提交：不依赖调用方事务，也不受并发未提交窗口影响；
    只读端点与本体编辑事务均可调用。
    """
    if await _get_scene(db, workspace_id, domain) is not None:
        return True
    await _ensure_seeded_in_new_session(workspace_id, domain)
    scene = await _get_scene(db, workspace_id, domain)
    if scene is None:
        logger.warning(
            "[PreferenceType] domain missing after seed workspace=%s domain=%s",
            workspace_id,
            domain,
        )
    return scene is not None
