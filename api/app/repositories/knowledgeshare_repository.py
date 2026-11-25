import uuid
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload
from app.models.knowledgeshare_model import KnowledgeShare
from app.repositories.knowledge_repository import knowledge_schema_load_options
from app.schemas import knowledgeshare_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


def _knowledgeshare_schema_load_options():
    return (
        selectinload(KnowledgeShare.target_kb).options(*knowledge_schema_load_options()),
        selectinload(KnowledgeShare.target_workspace),
        selectinload(KnowledgeShare.shared_user),
    )


def get_knowledgeshares_paginated(
        db: Session,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """
    Paged query knowledge base sharing (with filtering and sorting)
    """
    db_logger.debug(
        f"Query knowledge base sharing in pages: page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}, filters_count={len(filters)}")

    try:
        query = db.query(KnowledgeShare)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Calculate the total count (for pagination)
        total = query.count()
        db_logger.debug(f"Total number of knowledge base sharing queries: {total}")

        # sort
        if orderby:
            order_attr = getattr(KnowledgeShare, orderby, None)
            if order_attr is not None:
                if desc:
                    query = query.order_by(order_attr.desc())
                else:
                    query = query.order_by(order_attr.asc())
                db_logger.debug(f"sort: {orderby}, desc={desc}")

        # pagination
        items = query.offset((page - 1) * pagesize).limit(pagesize).all()
        db_logger.info(f"The knowledge base sharing paging query has been successful: total={total}, Number of current page={len(items)}")

        return total, [knowledgeshare_schema.KnowledgeShare.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(f"Querying knowledge base sharing pagination failed: page={page}, pagesize={pagesize} - {str(e)}")
        raise


async def get_knowledgeshares_paginated_async(
        db: AsyncSession,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """Async version of get_knowledgeshares_paginated."""
    try:
        stmt = select(KnowledgeShare).options(*_knowledgeshare_schema_load_options())
        for filter_cond in filters:
            stmt = stmt.where(filter_cond)

        total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total = total_result.scalar_one()

        if orderby:
            order_attr = getattr(KnowledgeShare, orderby, None)
            if order_attr is not None:
                stmt = stmt.order_by(order_attr.desc() if desc else order_attr.asc())

        result = await db.execute(stmt.offset((page - 1) * pagesize).limit(pagesize))
        items = result.scalars().all()
        return total, [knowledgeshare_schema.KnowledgeShare.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(f"Querying knowledge base sharing pagination failed (async): page={page}, pagesize={pagesize} - {str(e)}")
        raise


def get_source_kb_ids_by_target_kb_id(
        db: Session,
        filters: list
) -> list:
    """
    Query the original knowledge base ID list by sharing the knowledge base
    Return: list[(source_kb_id,source_workspace_id)] - List of knowledge base source_kb_id and source_workspace_id
    """
    db_logger.debug(
        f"Query the original knowledge base id list by sharing the knowledge base: filters_count={len(filters)}")

    try:
        # Only query the id field
        query = db.query(KnowledgeShare.source_kb_id, KnowledgeShare.source_workspace_id)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Get all IDs
        items = query.all()
        db_logger.info(f"Successfully queried the original knowledge base ID list by sharing the knowledge base: count={len(items)}")

        # Return the list of source_kb_id and source_workspace_id directly. Since only the source_kb_id and source_workspace_id field is queried
        return items
    except Exception as e:
        db_logger.error(f"Failed to query the original knowledge base ID list through knowledge base sharing: {str(e)}")
        raise


async def get_source_kb_ids_by_target_kb_id_async(
        db: AsyncSession,
        filters: list
) -> list:
    """Async version of get_source_kb_ids_by_target_kb_id."""
    try:
        stmt = select(KnowledgeShare.source_kb_id, KnowledgeShare.source_workspace_id)
        for filter_cond in filters:
            stmt = stmt.where(filter_cond)
        result = await db.execute(stmt)
        return result.all()
    except Exception as e:
        db_logger.error(f"Failed to query source KB IDs through knowledge share (async): {str(e)}")
        raise


def create_knowledgeshare(db: Session, knowledgeshare: knowledgeshare_schema.KnowledgeShareCreate) -> KnowledgeShare:
    db_logger.debug(f"Create a knowledge base sharing record: source_kb_id={knowledgeshare.source_kb_id}")

    try:
        db_knowledgeshare = KnowledgeShare(**knowledgeshare.model_dump())
        db.add(db_knowledgeshare)
        db.commit()
        db_logger.info(f"knowledge base sharing record created successfully: (ID: {db_knowledgeshare.id})")
        return db_knowledgeshare
    except Exception as e:
        db_logger.error(f"Failed to create a knowledge base sharing record: source_kb_id={knowledgeshare.source_kb_id} - {str(e)}")
        db.rollback()
        raise


async def create_knowledgeshare_async(
        db: AsyncSession,
        knowledgeshare: knowledgeshare_schema.KnowledgeShareCreate
) -> KnowledgeShare:
    """Async version of create_knowledgeshare."""
    try:
        db_knowledgeshare = KnowledgeShare(**knowledgeshare.model_dump())
        db.add(db_knowledgeshare)
        await db.commit()
        await db.refresh(db_knowledgeshare)
        loaded_share = await get_knowledgeshare_by_id_async(db, db_knowledgeshare.id)
        return loaded_share or db_knowledgeshare
    except Exception as e:
        db_logger.error(
            f"Failed to create a knowledge base sharing record (async): "
            f"source_kb_id={knowledgeshare.source_kb_id} - {str(e)}"
        )
        await db.rollback()
        raise


def get_knowledgeshare_by_id(db: Session, knowledgeshare_id: uuid.UUID) -> KnowledgeShare | None:
    db_logger.debug(f"Query knowledge base sharing based on ID: knowledgeshare_id={knowledgeshare_id}")

    try:
        knowledgeshare = db.query(KnowledgeShare).filter(
            or_(
                KnowledgeShare.id == knowledgeshare_id,
                KnowledgeShare.target_kb_id == knowledgeshare_id
            )
        ).first()
        if knowledgeshare:
            db_logger.debug(f"knowledge base sharing query successful: (ID: {knowledgeshare_id})")
        else:
            db_logger.debug(f"knowledge base sharing does not exist: knowledgeshare_id={knowledgeshare_id}")
        return knowledgeshare
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base sharing based on the ID: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


def get_knowledgeshare_by_id_in_source_workspace(
        db: Session,
        knowledgeshare_id: uuid.UUID,
        source_workspace_id: uuid.UUID,
) -> KnowledgeShare | None:
    """Return a share only to its source workspace."""
    try:
        return (
            db.query(KnowledgeShare)
            .filter(
                or_(
                    KnowledgeShare.id == knowledgeshare_id,
                    KnowledgeShare.target_kb_id == knowledgeshare_id,
                ),
                KnowledgeShare.source_workspace_id == source_workspace_id,
            )
            .first()
        )
    except Exception as e:
        db_logger.error(
            "Failed to query knowledge share in source workspace: share_id=%s workspace_id=%s error=%s",
            knowledgeshare_id,
            source_workspace_id,
            str(e),
        )
        raise


async def get_knowledgeshare_by_id_async(db: AsyncSession, knowledgeshare_id: uuid.UUID) -> KnowledgeShare | None:
    """Async version of get_knowledgeshare_by_id."""
    try:
        stmt = select(KnowledgeShare).options(*_knowledgeshare_schema_load_options()).where(
            or_(
                KnowledgeShare.id == knowledgeshare_id,
                KnowledgeShare.target_kb_id == knowledgeshare_id,
            )
        )
        result = await db.execute(stmt)
        return result.scalars().first()
    except Exception as e:
        db_logger.error(f"Failed to query knowledge share by ID (async): knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


async def get_knowledgeshare_by_id_in_source_workspace_async(
        db: AsyncSession,
        knowledgeshare_id: uuid.UUID,
        source_workspace_id: uuid.UUID,
) -> KnowledgeShare | None:
    """Async source-workspace-scoped knowledge share lookup."""
    try:
        stmt = select(KnowledgeShare).options(*_knowledgeshare_schema_load_options()).where(
            or_(
                KnowledgeShare.id == knowledgeshare_id,
                KnowledgeShare.target_kb_id == knowledgeshare_id,
            ),
            KnowledgeShare.source_workspace_id == source_workspace_id,
        )
        result = await db.execute(stmt)
        return result.scalars().first()
    except Exception as e:
        db_logger.error(
            "Failed to query knowledge share in source workspace (async): share_id=%s workspace_id=%s error=%s",
            knowledgeshare_id,
            source_workspace_id,
            str(e),
        )
        raise


def delete_knowledgeshare_by_id(db: Session, knowledgeshare_id: uuid.UUID):
    db_logger.debug(f"Delete knowledge base sharing record: knowledgeshare_id={knowledgeshare_id}")

    try:
        result = db.query(KnowledgeShare).filter(
            or_(
                KnowledgeShare.id == knowledgeshare_id,
                KnowledgeShare.target_kb_id == knowledgeshare_id
            )
        ).delete()
        db.commit()

        if result > 0:
            db_logger.info(f"knowledge base sharing record deleted successfully: (ID: {knowledgeshare_id})")
        else:
            db_logger.warning(f"The knowledge base sharing record does not exist, and cannot be deleted: knowledgeshare_id={knowledgeshare_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete knowledge base sharing record: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        db.rollback()
        raise


async def delete_knowledgeshare_by_id_async(db: AsyncSession, knowledgeshare_id: uuid.UUID):
    """Async version of delete_knowledgeshare_by_id."""
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
        if result.rowcount and result.rowcount > 0:
            db_logger.info(f"knowledge base sharing record deleted successfully (async): (ID: {knowledgeshare_id})")
        else:
            db_logger.warning(f"The knowledge base sharing record does not exist, and cannot be deleted (async): knowledgeshare_id={knowledgeshare_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete knowledge share record (async): knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        await db.rollback()
        raise


async def delete_knowledgeshare_by_id_in_source_workspace_async(
        db: AsyncSession,
        knowledgeshare_id: uuid.UUID,
        source_workspace_id: uuid.UUID,
) -> int:
    """Delete a share only after its source workspace has been constrained."""
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
    except Exception as e:
        await db.rollback()
        db_logger.error(
            "Failed to delete knowledge share in source workspace: share_id=%s workspace_id=%s error=%s",
            knowledgeshare_id,
            source_workspace_id,
            str(e),
        )
        raise
