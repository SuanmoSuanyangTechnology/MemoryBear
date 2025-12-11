import uuid
from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.knowledgeshare_model import KnowledgeShare
from app.schemas.knowledgeshare_schema import KnowledgeShareCreate
from app.repositories import knowledgeshare_repository
from app.core.logging_config import get_business_logger

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()


def get_knowledgeshares_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(f"Query knowledge base sharing in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")

    try:
        total, items = knowledgeshare_repository.get_knowledgeshares_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc
        )
        business_logger.info(f"The knowledge base sharing paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying knowledge base sharing pagination failed: username={current_user.username} - {str(e)}")
        raise


def get_source_kb_ids_by_target_kb_id(
        db: Session,
        current_user: User,
        filters: list
) -> list:
    business_logger.debug(f"Query the original knowledge base id list by sharing the knowledge base: username={current_user.username}")

    try:
        items = knowledgeshare_repository.get_source_kb_ids_by_target_kb_id(
            db=db,
            filters=filters
        )
        business_logger.info(f"Successfully queried the original knowledge base ID list by sharing the knowledge base: username={current_user.username} count={len(items)}")
        return items
    except Exception as e:
        business_logger.error(f"Failed to query the original knowledge base ID list through knowledge base sharing: username={current_user.username} - {str(e)}")
        raise


def create_knowledgeshare(
        db: Session, knowledgeshare: KnowledgeShareCreate, current_user: User
) -> KnowledgeShare:
    business_logger.info(f"Create a knowledge base sharing: creator: {current_user.username}")

    try:
        knowledgeshare.source_workspace_id = current_user.current_workspace_id
        knowledgeshare.shared_by = current_user.id
        business_logger.debug("Start creating a knowledge base sharing")
        db_knowledgeshare = knowledgeshare_repository.create_knowledgeshare(
            db=db, knowledgeshare=knowledgeshare
        )
        business_logger.info(f"knowledge base sharing created successfully: (ID: {db_knowledgeshare.id}), creator: {current_user.username}")
        return db_knowledgeshare
    except Exception as e:
        business_logger.error(f"Failed to create a knowledge base sharing - {str(e)}")
        raise


def get_knowledgeshare_by_id(db: Session, knowledgeshare_id: uuid.UUID, current_user: User) -> KnowledgeShare | None:
    business_logger.debug(f"Query knowledge base sharing based on ID: knowledgeshare_id={knowledgeshare_id}, username: {current_user.username}")

    try:
        knowledgeshare = knowledgeshare_repository.get_knowledgeshare_by_id(db=db, knowledgeshare_id=knowledgeshare_id)
        if knowledgeshare:
            business_logger.info(f"knowledge base sharing query successful: (ID: {knowledgeshare_id})")
        else:
            business_logger.warning(f"knowledge base sharing does not exist: knowledgeshare_id={knowledgeshare_id}")
        return knowledgeshare
    except Exception as e:
        business_logger.error(f"Failed to query the knowledge base sharing: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


def delete_knowledgeshare_by_id(db: Session, knowledgeshare_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete knowledge base sharing: knowledgeshare_id={knowledgeshare_id}, operator: {current_user.username}")

    try:
        # First, query the knowledge base sharing information for logging purposes
        knowledgeshare = knowledgeshare_repository.get_knowledgeshare_by_id(db=db, knowledgeshare_id=knowledgeshare_id)
        if knowledgeshare:
            business_logger.debug(f"Execute knowledge base sharing deletion: (ID: {knowledgeshare_id})")
        else:
            business_logger.warning(f"The knowledge base sharing does not exist: knowledgeshare_id={knowledgeshare_id}")

        knowledgeshare_repository.delete_knowledgeshare_by_id(db=db, knowledgeshare_id=knowledgeshare_id)
        business_logger.info(f"knowledge base sharing deleted successfully: knowledgeshare_id={knowledgeshare_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete knowledge base sharing: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise
