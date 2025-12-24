import uuid
from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.knowledge_model import Knowledge
from app.schemas.knowledge_schema import KnowledgeCreate, KnowledgeUpdate
from app.repositories import knowledge_repository
from app.core.logging_config import get_business_logger

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()


def get_knowledges_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(f"Query knowledge base in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")
    
    try:
        total, items = knowledge_repository.get_knowledges_paginated(
                db=db,
                filters=filters,
                page=page,
                pagesize=pagesize,
                orderby=orderby,
                desc=desc
            )
        business_logger.info(f"The knowledge base paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying knowledge base pagination failed: username={current_user.username} - {str(e)}")
        raise


def get_chunded_knowledgeids(
        db: Session,
        current_user: User,
        filters: list
) -> list:
    business_logger.debug(f"Query the list of vectorized knowledge base IDs: username={current_user.username}")

    try:
        items = knowledge_repository.get_chunked_knowledgeids(
            db=db,
            filters=filters
        )
        business_logger.info(f"Querying the vectorized knowledge base id list succeeded: username={current_user.username} count={len(items)}")
        return items
    except Exception as e:
        business_logger.error(f"Querying the vectorized knowledge base id list failed: username={current_user.username} - {str(e)}")
        raise


def create_knowledge(
        db: Session, knowledge: KnowledgeCreate, current_user: User
) -> Knowledge:
    business_logger.info(f"Create a knowledge base: {knowledge.name}, creator: {current_user.username}")
    
    try:
        knowledge.created_by = current_user.id
        if knowledge.workspace_id is None:
            knowledge.workspace_id = current_user.current_workspace_id
        if knowledge.parent_id is None:
            knowledge.parent_id = knowledge.workspace_id
        business_logger.debug(f"Start creating the knowledge base: {knowledge.name}")
        db_knowledge = knowledge_repository.create_knowledge(
            db=db, knowledge=knowledge
        )
        business_logger.info(f"The knowledge base has been successfully created: {knowledge.name} (ID: {db_knowledge.id}), creator: {current_user.username}")
        return db_knowledge
    except Exception as e:
        business_logger.error(f"Failed to create a knowledge base: {knowledge.name} - {str(e)}")
        raise


def get_knowledge_by_id(db: Session, knowledge_id: uuid.UUID, current_user: User) -> Knowledge | None:
    business_logger.debug(f"Query knowledge base based on ID: knowledge_id={knowledge_id}, username: {current_user.username}")
    
    try:
        knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=knowledge_id)
        if knowledge:
            business_logger.info(f"knowledge base query successful: {knowledge.name} (ID: {knowledge_id})")
        else:
            business_logger.warning(f"knowledge base does not exist: knowledge_id={knowledge_id}")
        return knowledge
    except Exception as e:
        business_logger.error(f"Failed to query the knowledge base based on the ID: knowledge_id={knowledge_id} - {str(e)}")
        raise


def get_knowledge_by_name(db: Session, name: str, current_user: User) -> Knowledge | None:
    business_logger.debug(f"Query knowledge base based on name: name={name}, username: {current_user.username}")

    try:
        knowledge = knowledge_repository.get_knowledge_by_name(db=db, name=name, workspace_id=current_user.current_workspace_id)
        if knowledge:
            business_logger.info(f"knowledge base query successful: {name} (ID: {knowledge.id})")
        else:
            business_logger.warning(f"knowledge base does not exist: name={name}")
        return knowledge
    except Exception as e:
        business_logger.error(f"Failed to query the knowledge base based on the name: name={name} - {str(e)}")
        raise


def delete_knowledge_by_id(db: Session, knowledge_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete knowledge base: knowledge_id={knowledge_id}, operator: {current_user.username}")
    
    try:
        # First, query the knowledge base information for logging purposes
        knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=knowledge_id)
        if knowledge:
            business_logger.debug(f"Execute knowledge base deletion: {knowledge.name} (ID: {knowledge_id})")
        else:
            business_logger.warning(f"The knowledge base to be deleted does not exist: knowledge_id={knowledge_id}")
        
        knowledge_repository.delete_knowledge_by_id(db=db, knowledge_id=knowledge_id)
        business_logger.info(f"knowledge base record deleted successfully: knowledge_id={knowledge_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete knowledge base: knowledge_id={knowledge_id} - {str(e)}")
        raise
