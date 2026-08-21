"""File repository migrated to the service async database boundary."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.schemas.file import FileCreate
from ..models.owned import File, Knowledge

logger = logging.getLogger(__name__)


async def get_files_paginated_async(
    db: AsyncSession,
    filters: list,
    page: int,
    pagesize: int,
    orderby: str | None = None,
    desc: bool = False,
) -> tuple[int, list[File]]:
    stmt = select(File)
    for filter_cond in filters:
        stmt = stmt.where(filter_cond)
    total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()
    if orderby:
        order_attr = getattr(File, orderby, None)
        if order_attr is not None:
            stmt = stmt.order_by(order_attr.desc() if desc else order_attr.asc())
    result = await db.execute(stmt.offset((page - 1) * pagesize).limit(pagesize))
    return total, list(result.scalars().all())


async def create_file_async(db: AsyncSession, file: FileCreate) -> File:
    try:
        db_file = File(**file.model_dump())
        db.add(db_file)
        await db.commit()
        await db.refresh(db_file)
        return db_file
    except Exception:
        await db.rollback()
        logger.exception("Failed to create file: file_name=%s", file.file_name)
        raise


async def get_file_by_id_async(db: AsyncSession, file_id: uuid.UUID) -> File | None:
    result = await db.execute(select(File).where(File.id == file_id))
    return result.scalars().first()


async def get_file_by_id_in_workspace_async(
    db: AsyncSession,
    file_id: uuid.UUID,
    workspace_id: uuid.UUID,
    kb_id: uuid.UUID | None = None,
) -> File | None:
    stmt = (
        select(File)
        .join(Knowledge, File.kb_id == Knowledge.id)
        .where(
            File.id == file_id,
            Knowledge.workspace_id == workspace_id,
        )
    )
    if kb_id is not None:
        stmt = stmt.where(File.kb_id == kb_id)
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_files_by_parent_id_async(
    db: AsyncSession,
    parent_id: uuid.UUID | None,
) -> list[File]:
    stmt = select(File)
    if parent_id:
        stmt = stmt.where(File.parent_id == parent_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_file_by_id_async(db: AsyncSession, file_id: uuid.UUID) -> int:
    try:
        result = await db.execute(delete(File).where(File.id == file_id))
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete file: file_id=%s", file_id)
        raise
