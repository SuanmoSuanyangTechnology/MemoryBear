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


def _scene_exists(db: Session, workspace_id: uuid.UUID, domain: str) -> bool:
    row = db.execute(
        select(OntologyPreferenceScene.preference_scene_id).where(
            OntologyPreferenceScene.workspace_id == workspace_id,
            OntologyPreferenceScene.domain == domain,
        )
    ).first()
    return row is not None


async def _scene_exists_async(
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
    if _scene_exists(db, workspace_id, domain):
        return False

    scene, classes = build_seed_rows(workspace_id, domain)

    if not commit:
        try:
            with db.begin_nested():
                db.add(scene)
                for item in classes:
                    db.add(item)
                db.flush()
        except IntegrityError as exc:
            # 并发首次种入：唯一约束兜底，savepoint 回滚后视为已种入。
            logger.warning(
                "[PreferenceSeed] concurrent insert skipped workspace=%s domain=%s error=%s",
                workspace_id,
                domain,
                exc,
            )
            return False
        return True

    db.add(scene)
    for item in classes:
        db.add(item)

    try:
        db.commit()
    except IntegrityError as exc:
        # 并发首次种入：唯一约束兜底，回滚后视为已种入。
        db.rollback()
        logger.warning(
            "[PreferenceSeed] concurrent insert skipped workspace=%s domain=%s error=%s",
            workspace_id,
            domain,
            exc,
        )
        return False
    except Exception:
        # 其它写入异常也要回滚，避免 failed session 传染后续操作
        db.rollback()
        raise
    return True


async def ensure_seeded_async(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    domain: str = "coding",
    commit: bool = True,
) -> bool:
    """异步会话版本：供页面接口与本体绑定校验使用。

    commit=True 仅用于自有会话（请求级只读接口、初始化流程），调用前不应有
    其它未提交改动；已有未提交改动时传 commit=False。
    commit=False 时用 SAVEPOINT 只写入种子行，不提交调用方事务；
    并发唯一键冲突只回滚 savepoint，调用方后续写入不受影响。
    """
    if await _scene_exists_async(db, workspace_id, domain):
        return False

    scene, classes = build_seed_rows(workspace_id, domain)

    if not commit:
        try:
            async with db.begin_nested():
                db.add(scene)
                for item in classes:
                    db.add(item)
                await db.flush()
        except IntegrityError as exc:
            # 并发首次种入：唯一约束兜底，savepoint 回滚后视为已种入。
            logger.warning(
                "[PreferenceSeed] concurrent insert skipped workspace=%s domain=%s error=%s",
                workspace_id,
                domain,
                exc,
            )
            return False
        return True

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
    except Exception:
        # 其它写入异常也要回滚，避免 failed session 传染后续操作
        await db.rollback()
        raise
    return True
