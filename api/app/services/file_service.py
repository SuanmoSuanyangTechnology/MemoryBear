import uuid
from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.file_model import File
from app.schemas.file_schema import FileCreate, FileUpdate
from app.repositories import file_repository
from app.core.logging_config import get_business_logger

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()


def get_files_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(f"Query file in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")

    try:
        total, items = file_repository.get_files_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc
        )
        business_logger.info(f"The file paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying file pagination failed: username={current_user.username} - {str(e)}")
        raise


def create_file(
        db: Session, file: FileCreate, current_user: User
) -> File:
    business_logger.info(f"Create a file: {file.file_name}, creator: {current_user.username}")

    try:
        file.created_by = current_user.id
        if file.parent_id is None:
            file.parent_id = file.kb_id
        db_file = file_repository.create_file(
            db=db, file=file
        )
        business_logger.info(f"The file has been successfully created: {file.file_name} (ID: {db_file.id}), creator: {current_user.username}")
        return db_file
    except Exception as e:
        business_logger.error(f"Failed to create a file: {file.file_name} - {str(e)}")
        raise


def get_file_by_id(db: Session, file_id: uuid.UUID) -> File | None:
    business_logger.debug(f"Query file based on ID: file_id={file_id}")

    try:
        file = file_repository.get_file_by_id(db=db, file_id=file_id)
        if file:
            business_logger.info(f"file query successful: {file.file_name} (ID: {file_id})")
        else:
            business_logger.warning(f"file does not exist: file_id={file_id}")
        return file
    except Exception as e:
        business_logger.error(f"Failed to query the file based on the ID: file_id={file_id} - {str(e)}")
        raise


def get_files_by_parent_id(db: Session, parent_id: uuid.UUID | None, current_user: User) -> list | None:
    business_logger.debug(f"Query file based on folder ID: parent_id={parent_id}, username: {current_user.username}")
    return file_repository.get_files_by_parent_id(db=db, parent_id=parent_id)


def delete_file_by_id(db: Session, file_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete file: file_id={file_id}, operator: {current_user.username}")

    try:
        file_repository.delete_file_by_id(db=db, file_id=file_id)
        business_logger.info(f"file_id deleted successfully: file_id={file_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete file: file_id={file_id} - {str(e)}")
        raise
