import uuid
from sqlalchemy.orm import Session
from app.models.knowledgeshare_model import KnowledgeShare
from app.schemas import knowledgeshare_schema
from app.core.logging_config import get_db_logger
from sqlalchemy.orm import joinedload
from sqlalchemy import or_

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


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


def get_source_kb_ids_by_target_kb_id(
        db: Session,
        filters: list
) -> list:
    """
    Query the original knowledge base ID list by sharing the knowledge base
    Return: list[UUID] - List of knowledge base IDs
    """
    db_logger.debug(
        f"Query the original knowledge base id list by sharing the knowledge base: filters_count={len(filters)}")

    try:
        # Only query the id field
        query = db.query(KnowledgeShare.source_kb_id)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Get all IDs
        items = query.all()
        db_logger.info(f"Successfully queried the original knowledge base ID list by sharing the knowledge base: count={len(items)}")

        # Return the list of IDs directly. Since only the ID field is queried, the returned data is a single column
        return [item[0] for item in items]
    except Exception as e:
        db_logger.error(f"Failed to query the original knowledge base ID list through knowledge base sharing: {str(e)}")
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
