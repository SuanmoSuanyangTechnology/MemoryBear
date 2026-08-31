"""Knowledge repository migrated from the legacy API service."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.schemas.knowledge import KnowledgeCreate
from ..models.owned import Document, Knowledge, PermissionType
from ..rag.parser_config import (
    build_default_knowledge_parser_config,
    normalize_new_knowledge_parser_config,
)

logger = logging.getLogger(__name__)


def _knowledge_values(
    knowledge: KnowledgeCreate,
    *,
    preserve_source_parser_config: bool = False,
) -> dict[str, Any]:
    values = knowledge.model_dump()
    if preserve_source_parser_config:
        if values.get("parser_config") is None:
            values["parser_config"] = build_default_knowledge_parser_config()
    else:
        values["parser_config"] = normalize_new_knowledge_parser_config(
            values.get("parser_config")
        )
    return values


async def get_knowledges_paginated_async(
    db: AsyncSession,
    filters: list,
    page: int,
    pagesize: int,
    orderby: str | None = None,
    desc: bool = False,
) -> tuple[int, list[Knowledge]]:
    stmt = select(Knowledge)
    for filter_cond in filters:
        stmt = stmt.where(filter_cond)

    total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()
    if orderby:
        order_attr = getattr(Knowledge, orderby, None)
        if order_attr is not None:
            stmt = stmt.order_by(order_attr.desc() if desc else order_attr.asc())

    result = await db.execute(stmt.offset((page - 1) * pagesize).limit(pagesize))
    return total, list(result.scalars().all())


async def create_knowledge_async(
    db: AsyncSession,
    knowledge: KnowledgeCreate,
    *,
    preserve_source_parser_config: bool = False,
) -> Knowledge:
    try:
        db_knowledge = Knowledge(
            **_knowledge_values(
                knowledge,
                preserve_source_parser_config=preserve_source_parser_config,
            )
        )
        db.add(db_knowledge)
        await db.commit()
        await db.refresh(db_knowledge)
        return db_knowledge
    except Exception:
        await db.rollback()
        logger.exception("Failed to create knowledge: name=%s", knowledge.name)
        raise


async def get_knowledge_by_id_async(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
) -> Knowledge | None:
    result = await db.execute(select(Knowledge).where(Knowledge.id == knowledge_id))
    return result.scalars().first()


async def get_knowledge_by_id_in_workspace_async(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Knowledge | None:
    result = await db.execute(
        select(Knowledge).where(
            Knowledge.id == knowledge_id,
            Knowledge.workspace_id == workspace_id,
        )
    )
    return result.scalars().first()


async def get_knowledge_by_external_id_async(
    db: AsyncSession,
    external_id: str,
    workspace_id: uuid.UUID,
) -> Knowledge | None:
    result = await db.execute(
        select(Knowledge).where(
            Knowledge.external_id == external_id,
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
        )
    )
    return result.scalars().first()


async def get_knowledges_by_parent_ids_async(
    db: AsyncSession,
    parent_ids: list[uuid.UUID],
    workspace_id: uuid.UUID,
) -> list[Knowledge]:
    if not parent_ids:
        return []
    result = await db.execute(
        select(Knowledge).where(
            Knowledge.parent_id.in_(parent_ids),
            Knowledge.workspace_id == workspace_id,
            Knowledge.status != 2,
            Knowledge.permission_id != PermissionType.Memory,
        )
    )
    return list(result.scalars().all())


async def get_document_counts_by_knowledge_ids_async(
    db: AsyncSession,
    knowledge_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not knowledge_ids:
        return {}
    unique_knowledge_ids = list(dict.fromkeys(knowledge_ids))
    result = await db.execute(
        select(Document.kb_id, func.count(Document.id))
        .where(
            Document.kb_id.in_(unique_knowledge_ids),
            Document.status == 1,
        )
        .group_by(Document.kb_id)
    )
    return {kb_id: int(count) for kb_id, count in result.all()}


async def get_knowledge_by_name_async(
    db: AsyncSession,
    name: str,
    workspace_id: uuid.UUID,
) -> Knowledge | None:
    result = await db.execute(
        select(Knowledge).where(
            Knowledge.name == name,
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
        )
    )
    return result.scalars().first()


async def delete_knowledge_by_id_async(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
) -> int:
    try:
        result = await db.execute(delete(Knowledge).where(Knowledge.id == knowledge_id))
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete knowledge: knowledge_id=%s", knowledge_id)
        raise
