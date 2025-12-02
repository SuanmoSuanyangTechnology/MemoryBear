import uuid
import datetime
from sqlalchemy.orm import Session
from app.models.document_model import Document
from app.schemas import document_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


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
            Document.progress_msg: "Pending",
            Document.process_duration: 0,
            Document.run: 0,  # Reset run status
            Document.updated_at: datetime.datetime.now()
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
