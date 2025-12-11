import uuid
from sqlalchemy.orm import Session
from app.models.file_model import File
from app.schemas import file_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


def get_files_paginated(
        db: Session,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """
    Paged query file (with filtering and sorting)
    """
    db_logger.debug(f"Query file in pages: page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}, filters_count={len(filters)}")
    
    try:
        query = db.query(File)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Calculate the total count (for pagination)
        total = query.count()
        db_logger.debug(f"Total number of file queries: {total}")

        # sort
        if orderby:
            order_attr = getattr(File, orderby, None)
            if order_attr is not None:
                if desc:
                    query = query.order_by(order_attr.desc())
                else:
                    query = query.order_by(order_attr.asc())
                db_logger.debug(f"sort: {orderby}, desc={desc}")

        # pagination
        items = query.offset((page - 1) * pagesize).limit(pagesize).all()
        db_logger.info(f"The file paging query has been successful: total={total}, Number of current page={len(items)}")

        return total, [file_schema.File.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(f"Querying file pagination failed: page={page}, pagesize={pagesize} - {str(e)}")
        raise


def create_file(db: Session, file: file_schema.FileCreate) -> File:
    db_logger.debug(f"Create a file record: filename={file.file_name}")
    
    try:
        db_file = File(**file.model_dump())
        db.add(db_file)
        db.commit()
        db_logger.info(f"File record created successfully: {file.file_name} (ID: {db_file.id})")
        return db_file
    except Exception as e:
        db_logger.error(f"Failed to create a file record: filename={file.file_name} - {str(e)}")
        db.rollback()
        raise


def get_file_by_id(db: Session, file_id: uuid.UUID) -> File | None:
    db_logger.debug(f"Query file based on ID: file_id={file_id}")
    
    try:
        file = db.query(File).filter(File.id == file_id).first()
        if file:
            db_logger.debug(f"File query successful: {file.file_name} (ID: {file_id})")
        else:
            db_logger.debug(f"File does not exist: file_id={file_id}")
        return file
    except Exception as e:
        db_logger.error(f"Failed to query the file based on the ID: file_id={file_id} - {str(e)}")
        raise


def get_files_by_parent_id(db: Session, parent_id: uuid.UUID | None) -> list | None:
    db_logger.debug(f"Query file based on folder ID: parent_id={parent_id}")
    
    try:
        query = db.query(File)
        if parent_id:
            query = query.filter(File.parent_id == parent_id)
        files = query.all()
        db_logger.debug(f"Folder query file successful: parent_id={parent_id}, file_num={len(files)}")
        return files
    except Exception as e:
        db_logger.error(f"Failed to query files based on folder ID: parent_id={parent_id} - {str(e)}")
        raise


def delete_file_by_id(db: Session, file_id: uuid.UUID):
    db_logger.debug(f"Delete file record: file_id={file_id}")
    
    try:
        # First, query the file information for logging purposes
        file = db.query(File).filter(File.id == file_id).first()
        if file:
            filename = file.file_name
        else:
            filename = "unknown"
            
        result = db.query(File).filter(File.id == file_id).delete()
        db.commit()
        
        if result > 0:
            db_logger.info(f"File record deleted successfully: {filename} (ID: {file_id})")
        else:
            db_logger.warning(f"The file record does not exist, and cannot be deleted: file_id={file_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete file record: file_id={file_id} - {str(e)}")
        db.rollback()
        raise
