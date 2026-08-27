"""Knowledge-share repository migrated from the legacy API service."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.schemas.knowledge_share import KnowledgeShareCreate
from ..models.owned import KnowledgeShare

logger = logging.getLogger(__name__)


async def get_knowledgeshares_paginated_async(
    db: AsyncSession,
    filters: list,
    page: int,
    pagesize: int,
    orderby: str | None = None,
    desc: bool = False,
) -> tuple[int, list[KnowledgeShare]]:
    stmt = select(KnowledgeShare)
    for filter_cond in filters:
        stmt = stmt.where(filter_cond)
    total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()
    if orderby:
        order_attr = getattr(KnowledgeShare, orderby, None)
        if order_attr is not None:
            stmt = stmt.order_by(order_attr.desc() if desc else order_attr.asc())
    result = await db.execute(stmt.offset((page - 1) * pagesize).limit(pagesize))
    return total, list(result.scalars().all())


async def get_source_kb_ids_by_target_kb_id_async(
    db: AsyncSession,
    filters: list,
) -> list:
    stmt = select(KnowledgeShare.source_kb_id, KnowledgeShare.source_workspace_id)
    for filter_cond in filters:
        stmt = stmt.where(filter_cond)
    result = await db.execute(stmt)
    return result.all()


async def create_knowledgeshare_async(
    db: AsyncSession,
    knowledgeshare: KnowledgeShareCreate,
) -> KnowledgeShare:
    try:
        db_share = KnowledgeShare(**knowledgeshare.model_dump())
        db.add(db_share)
        await db.commit()
        await db.refresh(db_share)
        return db_share
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create knowledge share: source_kb_id=%s",
            knowledgeshare.source_kb_id,
        )
        raise

async def get_knowledgeshare_by_id_async(
    db: AsyncSession,
    knowledgeshare_id: uuid.UUID,
) -> KnowledgeShare | None:
    result = await db.execute(
        select(KnowledgeShare).where(
            or_(
                KnowledgeShare.id == knowledgeshare_id,
                KnowledgeShare.target_kb_id == knowledgeshare_id,
            )
        )
    )
    return result.scalars().first()


async def get_knowledgeshare_by_id_in_source_workspace_async(
    db: AsyncSession,
    knowledgeshare_id: uuid.UUID,
    source_workspace_id: uuid.UUID,
) -> KnowledgeShare | None:
    result = await db.execute(
        select(KnowledgeShare).where(
            or_(
                KnowledgeShare.id == knowledgeshare_id,
                KnowledgeShare.target_kb_id == knowledgeshare_id,
            ),
            KnowledgeShare.source_workspace_id == source_workspace_id,
        )
    )
    return result.scalars().first()


async def delete_knowledgeshare_by_id_async(
    db: AsyncSession,
    knowledgeshare_id: uuid.UUID,
) -> int:
    try:
        result = await db.execute(
            delete(KnowledgeShare).where(
                or_(
                    KnowledgeShare.id == knowledgeshare_id,
                    KnowledgeShare.target_kb_id == knowledgeshare_id,
                )
            )
        )
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete knowledge share: share_id=%s", knowledgeshare_id)
        raise


async def delete_knowledgeshare_by_id_in_source_workspace_async(
    db: AsyncSession,
    knowledgeshare_id: uuid.UUID,
    source_workspace_id: uuid.UUID,
) -> int:
    try:
        result = await db.execute(
            delete(KnowledgeShare).where(
                or_(
                    KnowledgeShare.id == knowledgeshare_id,
                    KnowledgeShare.target_kb_id == knowledgeshare_id,
                ),
                KnowledgeShare.source_workspace_id == source_workspace_id,
            )
        )
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to delete knowledge share in workspace: share_id=%s workspace_id=%s",
            knowledgeshare_id,
            source_workspace_id,
        )
        raise
