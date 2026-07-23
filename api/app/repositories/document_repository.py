import uuid
from datetime import datetime
from typing import List
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from app.core.utils.datetime_utils import to_iso_z, utcnow, utcnow_naive
from app.models.document_model import Document
from app.schemas import document_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


def _pending_progress_msg() -> str:
    return f"{to_iso_z(utcnow())} Pending."


def get_documents_paginated(
        db: Session,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """
    Paged query document (with filtering and sorting)
    """
    db_logger.debug(f"Query documents in pages: page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}, filters_count={len(filters)}")
    
    try:
        query = db.query(Document)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Calculate the total count (for pagination)
        total = query.count()
        db_logger.debug(f"Total number of document queries: {total}")

        # sort
        if orderby:
            order_attr = getattr(Document, orderby, None)
            if order_attr is not None:
                if desc:
                    query = query.order_by(order_attr.desc())
                else:
                    query = query.order_by(order_attr.asc())
                db_logger.debug(f"sort: {orderby}, desc={desc}")

        # pagination
        items = query.offset((page - 1) * pagesize).limit(pagesize).all()
        db_logger.info(f"The document paging query has been successful: total={total}, Number of current page={len(items)}")

        return total, [document_schema.Document.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(f"Querying document pagination failed: page={page}, pagesize={pagesize} - {str(e)}")
        raise


async def get_documents_paginated_async(
        db: AsyncSession,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """Async version of get_documents_paginated."""
    db_logger.debug(
        f"Query documents in pages (async): page={page}, pagesize={pagesize}, "
        f"orderby={orderby}, desc={desc}, filters_count={len(filters)}"
    )

    try:
        stmt = select(Document)
        for filter_cond in filters:
            stmt = stmt.where(filter_cond)

        total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total = total_result.scalar_one()

        if orderby:
            order_attr = getattr(Document, orderby, None)
            if order_attr is not None:
                stmt = stmt.order_by(order_attr.desc() if desc else order_attr.asc())

        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)
        result = await db.execute(stmt)
        items = result.scalars().all()
        db_logger.info(
            f"The document paging query has been successful (async): "
            f"total={total}, Number of current page={len(items)}"
        )
        return total, [document_schema.Document.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(
            f"Querying document pagination failed (async): page={page}, pagesize={pagesize} - {str(e)}"
        )
        raise


def create_document(db: Session, document: document_schema.DocumentCreate) -> Document:
    db_logger.debug(f"Create a document record: file_name={document.file_name}")
    
    try:
        db_document = Document(**document.model_dump())
        db.add(db_document)
        db.commit()
        db_logger.info(f"Document record created successfully: {document.file_name} (ID: {db_document.id})")
        return db_document
    except Exception as e:
        db_logger.error(f"Failed to create a document record: title={document.file_name} - {str(e)}")
        db.rollback()
        raise


async def create_document_async(db: AsyncSession, document: document_schema.DocumentCreate) -> Document:
    """Async version of create_document."""
    db_logger.debug(f"Create a document record (async): file_name={document.file_name}")

    try:
        db_document = Document(**document.model_dump())
        db.add(db_document)
        await db.commit()
        await db.refresh(db_document)
        db_logger.info(f"Document record created successfully (async): {document.file_name} (ID: {db_document.id})")
        return db_document
    except Exception as e:
        db_logger.error(f"Failed to create a document record (async): title={document.file_name} - {str(e)}")
        await db.rollback()
        raise


def get_document_by_id(db: Session, document_id: uuid.UUID) -> Document | None:
    db_logger.debug(f"Query documents based on ID: document_id={document_id}")
    
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if document:
            db_logger.debug(f"Document query successful: {document.file_name} (ID: {document_id})")
        else:
            db_logger.debug(f"Document does not exist: document_id={document_id}")
        return document
    except Exception as e:
        db_logger.error(f"Failed to query the document based on the ID: document_id={document_id} - {str(e)}")
        raise


async def get_document_by_id_async(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    """Async version of get_document_by_id."""
    try:
        stmt = select(Document).where(Document.id == document_id)
        result = await db.execute(stmt)
        return result.scalars().first()
    except Exception as e:
        db_logger.error(f"Failed to query document by ID (async): document_id={document_id} - {str(e)}")
        raise


def reset_documents_progress_by_kb_id(db: Session, kb_id: uuid.UUID) -> int:
    """
    Reset the processing progress of all documents under the specified knowledge base

    Args:
        db: database session
        kb_id: Knowledge Base ID

    Returns:
        int: Number of updated documents
    """
    db_logger.debug(f"Reset the processing progress of all documents under the specified knowledge base: kb_id={kb_id}")
    try:
        # Build update conditions
        filters = [
            Document.kb_id == kb_id
        ]

        # Build updated data
        update_data = {
            Document.chunk_num: 0,
            Document.progress: 0,
            Document.progress_msg: _pending_progress_msg(),
            Document.process_duration: 0,
            Document.run: 0,  # Reset run status
            Document.updated_at: utcnow_naive()
        }

        # Perform batch update
        result = db.query(Document).filter(*filters).update(
            update_data,
            synchronize_session=False
        )

        # commit transaction
        db.commit()
        db_logger.debug(f"Successfully reset the processing progress of all documents under the specified knowledge base: kb_id: {kb_id}")
        return result

    except Exception as e:
        db.rollback()
        db_logger.error(f"Failed to reset the processing progress of all documents under the specified knowledge base: kb_id={kb_id} - {str(e)}")
        raise


async def reset_documents_progress_by_kb_id_async(db: AsyncSession, kb_id: uuid.UUID) -> int:
    """Async version of reset_documents_progress_by_kb_id."""
    db_logger.debug(f"Reset document processing progress by knowledge base (async): kb_id={kb_id}")
    try:
        update_data = {
            Document.chunk_num: 0,
            Document.progress: 0,
            Document.progress_msg: _pending_progress_msg(),
            Document.process_duration: 0,
            Document.run: 0,
            Document.updated_at: utcnow_naive(),
        }
        result = await db.execute(
            update(Document)
            .where(Document.kb_id == kb_id)
            .values(update_data)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return result.rowcount or 0
    except Exception as e:
        await db.rollback()
        db_logger.error(f"Failed to reset document progress by KB (async): kb_id={kb_id} - {str(e)}")
        raise



def delete_document_by_id(db: Session, document_id: uuid.UUID):
    db_logger.debug(f"Delete document record: document_id={document_id}")
    
    try:
        # First, query the document information for logging purposes
        document = db.query(Document).filter(Document.id == document_id).first()
        if document:
            file_name = document.file_name
        else:
            file_name = "unknown"
            
        result = db.query(Document).filter(Document.id == document_id).delete()
        db.commit()
        
        if result > 0:
            db_logger.info(f"Document record deleted successfully: {file_name} (ID: {document_id})")
        else:
            db_logger.warning(f"The document record does not exist, and cannot be deleted: document_id={document_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete document record: document_id={document_id} - {str(e)}")
        db.rollback()
        raise


async def delete_document_by_id_async(db: AsyncSession, document_id: uuid.UUID):
    """Async version of delete_document_by_id."""
    try:
        document = await get_document_by_id_async(db, document_id)
        file_name = document.file_name if document else "unknown"

        result = await db.execute(delete(Document).where(Document.id == document_id))
        await db.commit()

        if result.rowcount and result.rowcount > 0:
            db_logger.info(f"Document record deleted successfully (async): {file_name} (ID: {document_id})")
        else:
            db_logger.warning(f"The document record does not exist, and cannot be deleted (async): document_id={document_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete document record (async): document_id={document_id} - {str(e)}")
        await db.rollback()
        raise


async def get_total_chunk_by_file_names_async(db: AsyncSession, file_names: List[str]) -> int:
    """Async: get total chunk_num sum for given file names."""
    try:
        result = await db.execute(
            select(func.sum(Document.chunk_num)).where(
                Document.file_name.in_(file_names),
            )
        )
        return int(result.scalar() or 0)
    except Exception as e:
        db_logger.error(f"Failed to get total chunk by file names (async): count={len(file_names)} - {str(e)}")
        raise


async def get_total_chunk_by_file_names_before_date_async(
    db: AsyncSession, file_names: List[str], before_date: datetime,
) -> int:
    """Async: get total chunk_num sum for given file names created before a date."""
    try:
        result = await db.execute(
            select(func.sum(Document.chunk_num)).where(
                Document.file_name.in_(file_names),
                Document.created_at < before_date,
            )
        )
        return int(result.scalar() or 0)
    except Exception as e:
        db_logger.error(
            f"Failed to get total chunk by file names before date (async): "
            f"count={len(file_names)} - {str(e)}"
        )
        raise


async def get_documents_by_file_name_async(db: AsyncSession, file_name: str) -> List[Document]:
    """Async: get all documents with a given file name."""
    try:
        result = await db.execute(
            select(Document).where(Document.file_name == file_name)
        )
        return list(result.scalars().all())
    except Exception as e:
        db_logger.error(f"Failed to get documents by file name (async): file_name={file_name} - {str(e)}")
        raise


async def get_users_total_chunk_batch_async(db: AsyncSession, file_names: List[str]) -> dict:
    """Async: get total chunk_num grouped by file_name for given file names."""
    try:
        result = await db.execute(
            select(Document.file_name, func.sum(Document.chunk_num))
            .where(Document.file_name.in_(file_names))
            .group_by(Document.file_name)
        )
        return {file_name: int(total_chunk) for file_name, total_chunk in result.all()}
    except Exception as e:
        db_logger.error(f"Failed to get users total chunk batch (async): count={len(file_names)} - {str(e)}")
        raise
