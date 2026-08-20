"""Document repository migrated from the legacy API service."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.schemas.document import DocumentCreate
from ..models.owned import Document, Knowledge
from ..utils.datetime_utils import to_iso_z, utcnow, utcnow_naive

logger = logging.getLogger(__name__)


def _pending_progress_msg() -> str:
    return f"{to_iso_z(utcnow())} Pending."


async def get_documents_paginated_async(
    db: AsyncSession,
    filters: list,
    page: int,
    pagesize: int,
    orderby: str | None = None,
    desc: bool = False,
) -> tuple[int, list[Document]]:
    stmt = select(Document)
    for filter_cond in filters:
        stmt = stmt.where(filter_cond)
    total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()
    if orderby:
        order_attr = getattr(Document, orderby, None)
        if order_attr is not None:
            stmt = stmt.order_by(order_attr.desc() if desc else order_attr.asc())
    result = await db.execute(stmt.offset((page - 1) * pagesize).limit(pagesize))
    return total, list(result.scalars().all())


async def create_document_async(db: AsyncSession, document: DocumentCreate) -> Document:
    try:
        db_document = Document(**document.model_dump())
        db.add(db_document)
        await db.commit()
        await db.refresh(db_document)
        return db_document
    except Exception:
        await db.rollback()
        logger.exception("Failed to create document: file_name=%s", document.file_name)
        raise


async def get_document_by_id_async(
    db: AsyncSession,
    document_id: uuid.UUID,
) -> Document | None:
    result = await db.execute(select(Document).where(Document.id == document_id))
    return result.scalars().first()


async def get_document_by_id_in_workspace_async(
    db: AsyncSession,
    document_id: uuid.UUID,
    workspace_id: uuid.UUID,
    kb_id: uuid.UUID | None = None,
) -> Document | None:
    stmt = (
        select(Document)
        .join(Knowledge, Document.kb_id == Knowledge.id)
        .where(
            Document.id == document_id,
            Knowledge.workspace_id == workspace_id,
        )
    )
    if kb_id is not None:
        stmt = stmt.where(Document.kb_id == kb_id)
    result = await db.execute(stmt)
    return result.scalars().first()


async def reset_documents_progress_by_kb_id_async(
    db: AsyncSession,
    kb_id: uuid.UUID,
) -> int:
    try:
        result = await db.execute(
            update(Document)
            .where(Document.kb_id == kb_id)
            .values(
                {
                    Document.chunk_num: 0,
                    Document.progress: 0,
                    Document.progress_msg: _pending_progress_msg(),
                    Document.process_duration: 0,
                    Document.run: 0,
                    Document.updated_at: utcnow_naive(),
                }
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        logger.exception("Failed to reset document progress: kb_id=%s", kb_id)
        raise


async def delete_document_by_id_async(
    db: AsyncSession,
    document_id: uuid.UUID,
) -> int:
    try:
        result = await db.execute(delete(Document).where(Document.id == document_id))
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete document: document_id=%s", document_id)
        raise
