# -*- coding: utf-8 -*-
"""偏好类型幂等种入器

创建 workspace 时种入，页面读取 / 本体绑定 / 引擎加载时惰性补种。
不建 migration 数据回填；唯一约束 + IntegrityError 回滚兜住并发。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config.preference_type_seed import build_seed_rows
from app.models.ontology_preference import OntologyPreferenceScene

logger = logging.getLogger(__name__)


def _existing_scene_id(db: Session, workspace_id: uuid.UUID, domain: str) -> bool:
    row = db.execute(
        select(OntologyPreferenceScene.preference_scene_id).where(
            OntologyPreferenceScene.workspace_id == workspace_id,
            OntologyPreferenceScene.domain == domain,
        )
    ).first()
    return row is not None


async def _existing_scene_id_async(
    db: AsyncSession, workspace_id: uuid.UUID, domain: str
) -> bool:
    row = (
        await db.execute(
            select(OntologyPreferenceScene.preference_scene_id).where(
                OntologyPreferenceScene.workspace_id == workspace_id,
                OntologyPreferenceScene.domain == domain,
            )
        )
    ).first()
    return row is not None


def ensure_seeded(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    domain: str = "coding",
    commit: bool = True,
) -> bool:
    """确保 workspace 下存在指定偏好域的种子数据。

    Returns:
        True 表示本次发生了插入（或已交给调用方提交）；False 表示已存在 / 并发冲突。
    """
    if _existing_scene_id(db, workspace_id, domain):
        return False

    scene, classes = build_seed_rows(workspace_id, domain)
    db.add(scene)
    for item in classes:
        db.add(item)

    if not commit:
        return True

    try:
        db.commit()
    except IntegrityError:
        # 并发首次种入：唯一约束兜底，回滚后视为已种入。
        db.rollback()
        logger.warning(
            "[PreferenceSeed] concurrent insert skipped workspace=%s domain=%s",
            workspace_id,
            domain,
        )
        return False
    return True


async def ensure_seeded_async(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    domain: str = "coding",
) -> bool:
    """异步会话版本：供页面接口与本体绑定校验使用。"""
    if await _existing_scene_id_async(db, workspace_id, domain):
        return False

    scene, classes = build_seed_rows(workspace_id, domain)
    db.add(scene)
    for item in classes:
        db.add(item)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "[PreferenceSeed] concurrent insert skipped workspace=%s domain=%s",
            workspace_id,
            domain,
        )
        return False
    return True
