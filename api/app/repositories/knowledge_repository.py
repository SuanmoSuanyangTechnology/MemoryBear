import uuid
from sqlalchemy.orm import Session
from app.models.knowledge_model import Knowledge
from app.schemas import knowledge_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


def get_knowledges_paginated(
        db: Session,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """
    Paged query knowledge base (with filtering and sorting)
    """
    db_logger.debug(f"Query knowledge base in pages: page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}, filters_count={len(filters)}")
    
    try:
        query = db.query(Knowledge)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Calculate the total count (for pagination)
        total = query.count()
        db_logger.debug(f"Total number of knowledge base queries: {total}")

        # sort
        if orderby:
            order_attr = getattr(Knowledge, orderby, None)
            if order_attr is not None:
                if desc:
                    query = query.order_by(order_attr.desc())
                else:
                    query = query.order_by(order_attr.asc())
                db_logger.debug(f"sort: {orderby}, desc={desc}")

        # pagination
        items = query.offset((page - 1) * pagesize).limit(pagesize).all()
        db_logger.info(f"The knowledge base paging query has been successful: total={total}, Number of current page={len(items)}")

        return total, [knowledge_schema.Knowledge.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(f"Querying knowledge base pagination failed: page={page}, pagesize={pagesize} - {str(e)}")
        raise


def get_chunked_knowledgeids(
        db: Session,
        filters: list
) -> list:
    """
    Query the list of vectorized knowledge base IDs
    Return: list[UUID] - List of knowledge base IDs
    """
    db_logger.debug(f"Query the list of vectorized knowledge base IDs: filters_count={len(filters)}")

    try:
        # Only query the id field
        query = db.query(Knowledge.id)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Get all IDs
        items = query.all()
        db_logger.info(f"Querying the vectorized knowledge base id list succeeded: count={len(items)}")

        # Return the list of IDs directly. Since only the ID field is queried, the returned data is a single column
        return [item[0] for item in items]
    except Exception as e:
        db_logger.error(f"Querying the vectorized knowledge base id list failed: {str(e)}")
        raise


def create_knowledge(db: Session, knowledge: knowledge_schema.KnowledgeCreate) -> Knowledge:
    db_logger.debug(f"Create a knowledge base record: name={knowledge.name}")
    
    try:
        db_knowledge = Knowledge(**knowledge.model_dump())
        db.add(db_knowledge)
        db.commit()
        db_logger.info(f"knowledge base record created successfully: {knowledge.name} (ID: {db_knowledge.id})")
        return db_knowledge
    except Exception as e:
        db_logger.error(f"Failed to create a knowledge base record: name={knowledge.name} - {str(e)}")
        db.rollback()
        raise


def get_knowledge_by_id(db: Session, knowledge_id: uuid.UUID) -> Knowledge | None:
    db_logger.debug(f"Query knowledge base based on ID: knowledge_id={knowledge_id}")
    
    try:
        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful: {knowledge.name} (ID: {knowledge_id})")
        else:
            db_logger.debug(f"knowledge base does not exist: knowledge_id={knowledge_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the ID: knowledge_id={knowledge_id} - {str(e)}")
        raise


def get_knowledge_by_name(db: Session, name: str, workspace_id: uuid.UUID) -> Knowledge | None:
    db_logger.debug(f"Query knowledge base based on name and workspace_id: name={name}, workspace_id={workspace_id}")

    try:
        knowledge = db.query(Knowledge).filter(Knowledge.name == name,
                                               Knowledge.workspace_id == workspace_id,
                                               Knowledge.status == 1).first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful: {name} (ID: {knowledge.id})")
        else:
            db_logger.debug(f"knowledge base does not exist: name={name}, workspace_id={workspace_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the name and workspace_id: name={name}, workspace_id={workspace_id} - {str(e)}")
        raise


def delete_knowledge_by_id(db: Session, knowledge_id: uuid.UUID):
    db_logger.debug(f"Delete knowledge base record: knowledge_id={knowledge_id}")
    
    try:
        # First, query the knowledge base information for logging purposes
        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if knowledge:
            knowledge_name = knowledge.name
        else:
            knowledge_name = "unknown"
            
        result = db.query(Knowledge).filter(Knowledge.id == knowledge_id).delete()
        db.commit()
        
        if result > 0:
            db_logger.info(f"knowledge base record deleted successfully: {knowledge_name} (ID: {knowledge_id})")
        else:
            db_logger.warning(f"The knowledge base record does not exist, and cannot be deleted: knowledge_id={knowledge_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete knowledge base record: knowledge_id={knowledge_id} - {str(e)}")
        db.rollback()
        raise


def get_total_doc_num_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表所有doc_num的总和
    """
    db_logger.debug(f"Query total doc_num by workspace_id: workspace_id={workspace_id}")
    
    try:
        from sqlalchemy import func
        result = db.query(func.sum(Knowledge.doc_num)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).scalar()
        
        total = result if result is not None else 0
        db_logger.info(f"Total doc_num query successful: workspace_id={workspace_id}, total={total}")
        return total
    except Exception as e:
        db_logger.error(f"Failed to query total doc_num: workspace_id={workspace_id} - {str(e)}")
        raise


def get_total_chunk_num_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表所有chunk_num的总和
    """
    db_logger.debug(f"Query total chunk_num by workspace_id: workspace_id={workspace_id}")
    
    try:
        from sqlalchemy import func
        result = db.query(func.sum(Knowledge.chunk_num)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).scalar()
        
        total = result if result is not None else 0
        db_logger.info(f"Total chunk_num query successful: workspace_id={workspace_id}, total={total}")
        return total
    except Exception as e:
        db_logger.error(f"Failed to query total chunk_num: workspace_id={workspace_id} - {str(e)}")
        raise


def get_total_kb_count_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表所有不同id的数量（知识库总数）
    """
    db_logger.debug(f"Query total knowledge base count by workspace_id: workspace_id={workspace_id}")
    
    try:
        count = db.query(Knowledge).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).count()
        
        db_logger.info(f"Total knowledge base count query successful: workspace_id={workspace_id}, count={count}")
        return count
    except Exception as e:
        db_logger.error(f"Failed to query total knowledge base count: workspace_id={workspace_id} - {str(e)}")
        raise
