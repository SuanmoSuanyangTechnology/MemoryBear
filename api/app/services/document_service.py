import uuid
from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.document_model import Document
from app.schemas.document_schema import DocumentCreate, DocumentUpdate
from app.repositories import document_repository
from app.core.logging_config import get_business_logger

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()


def get_documents_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(f"Query document in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")

    try:
        total, items = document_repository.get_documents_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc
        )
        business_logger.info(f"The document paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying document pagination failed: username={current_user.username} - {str(e)}")
        raise


def create_document(
        db: Session, document: DocumentCreate, current_user: User
) -> Document:
    business_logger.info(f"Create a document: {document.file_name}, creator: {current_user.username}")

    try:
        document.created_by = current_user.id
        db_document = document_repository.create_document(
            db=db, document=document
        )
        business_logger.info(f"The document has been successfully created: {document.file_name} (ID: {db_document.id}), creator: {current_user.username}")
        return db_document
    except Exception as e:
        business_logger.error(f"Failed to create a document: {document.file_name} - {str(e)}")
        raise


def get_document_by_id(db: Session, document_id: uuid.UUID, current_user: User) -> Document | None:
    business_logger.debug(f"Query document based on ID: document_id={document_id}, username: {current_user.username}")

    try:
        document = document_repository.get_document_by_id(db=db, document_id=document_id)
        if document:
            business_logger.info(f"document query successful: {document.file_name} (ID: {document_id})")
        else:
            business_logger.warning(f"document does not exist: document_id={document_id}")
        return document
    except Exception as e:
        business_logger.error(f"Failed to query the document based on the ID: document_id={document_id} - {str(e)}")
        raise


def reset_documents_progress_by_kb_id(db: Session, kb_id: uuid.UUID, current_user: User) -> int:
    business_logger.debug(f"Reset the processing progress of all documents under the specified knowledge base: kb_id=={kb_id}, username: {current_user.username}")
    return document_repository.reset_documents_progress_by_kb_id(db=db, kb_id=kb_id)


def delete_document_by_id(db: Session, document_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete document: document_id={document_id}, operator: {current_user.username}")

    try:
        document_repository.delete_document_by_id(db=db, document_id=document_id)
        business_logger.info(f"document deleted successfully: document_id={document_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete document: document_id={document_id} - {str(e)}")
        raise
