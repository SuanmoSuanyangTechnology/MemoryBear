from typing import Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.models import knowledgeshare_model, knowledge_model
from app.schemas import knowledgeshare_schema, knowledge_schema
from app.schemas.response_schema import ApiResponse
from app.core.response_utils import success
from app.services import knowledgeshare_service, knowledge_service
from app.core.logging_config import get_api_logger

# Obtain a dedicated API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/knowledgeshares",
    tags=["knowledgeshares"],
    dependencies=[Depends(get_current_user)]  # Apply auth to all routes in this controller
)


@router.get("/{kb_id}/knowledgeshares", response_model=ApiResponse)
async def get_knowledgeshares(
        kb_id: uuid.UUID,
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at,updated_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Paged query knowledge base sharing list
    - Support filtering by kb_id
    - Support dynamic sorting
    - Return paging metadata + share list
    """
    api_logger.info(
        f"Query knowledge base sharing list: workspace_id={current_user.current_workspace_id}, kb_id={kb_id}, page={page}, pagesize={pagesize}, username: {current_user.username}")

    # 1. parameter validation
    if page < 1 or pagesize < 1:
        api_logger.warning(f"Error in paging parameters: page={page}, pagesize={pagesize}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Construct query conditions
    filters = [
        knowledgeshare_model.KnowledgeShare.source_workspace_id == current_user.current_workspace_id,
        knowledgeshare_model.KnowledgeShare.source_kb_id == kb_id
    ]

    # 3. Execute paged query
    try:
        api_logger.debug("Start executing knowledge base sharing and paging query")
        total, items = knowledgeshare_service.get_knowledgeshares_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            current_user=current_user
        )
        api_logger.info(f"Knowledge base sharing query successful: total={total}, returned={len(items)} records")
    except Exception as e:
        api_logger.error(f"Knowledge base sharing query failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )

    # 4. Return structured response
    result = {
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": True if page * pagesize < total else False
        }
    }
    return success(data=result, msg="Query of knowledge base sharing list successful")


@router.post("/knowledgeshare", response_model=ApiResponse)
async def create_knowledgeshare(
        create_data: knowledgeshare_schema.KnowledgeShareCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    create knowledgeshare
    """
    api_logger.info(
        f"Create a knowledge base sharing request: source_kb_id={create_data.source_kb_id}, source_workspace_id={current_user.current_workspace_id}, username: {current_user.username}")

    try:
        # 1.Create a knowledge base with permission_id=knowledge_model.PermissionType.Share
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=create_data.source_kb_id, current_user=current_user)
        knowledge = knowledge_schema.KnowledgeCreate(
            workspace_id=create_data.target_workspace_id,
            created_by=current_user.id,
            parent_id=create_data.target_workspace_id,
            name=db_knowledge.name,
            description=db_knowledge.description,
            avatar=db_knowledge.avatar,
            type=db_knowledge.type,
            permission_id=knowledge_model.PermissionType.Share,
            embedding_id=db_knowledge.embedding_id,
            reranker_id=db_knowledge.reranker_id,
            llm_id=db_knowledge.llm_id,
            image2text_id=db_knowledge.image2text_id,
            doc_num=db_knowledge.doc_num,
            chunk_num=db_knowledge.chunk_num,
            parser_id=db_knowledge.parser_id,
            parser_config=db_knowledge.parser_config
        )
        db_knowledge = knowledge_service.create_knowledge(db=db, knowledge=knowledge, current_user=current_user)
        # 2. Create a knowledge base for sharing
        api_logger.debug(f"Start creating the knowledge base sharing: {db_knowledge.name}")
        create_data.target_kb_id = db_knowledge.id
        db_knowledgeshare = knowledgeshare_service.create_knowledgeshare(db=db, knowledgeshare=create_data, current_user=current_user)
        api_logger.info(f"The knowledge base sharing has been successfully created: (ID: {db_knowledgeshare.id})")
        return success(data=knowledgeshare_schema.KnowledgeShare.model_validate(db_knowledgeshare), msg="The knowledge base sharing has been successfully created")
    except Exception as e:
        api_logger.error(f"The creation of the knowledge base sharing failed: {str(e)}")
        raise


@router.get("/{knowledgeshare_id}", response_model=ApiResponse)
async def get_knowledgeshare(
        knowledgeshare_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve knowledge base sharing information based on knowledgeshare_id
    """
    api_logger.info(f"Obtain details of the knowledge base sharing: knowledgeshare_id={knowledgeshare_id}, username: {current_user.username}")

    try:
        # 1. Query knowledge base sharing information from the database
        api_logger.debug(f"Query knowledge base sharing: {knowledgeshare_id}")
        db_knowledgeshare = knowledgeshare_service.get_knowledgeshare_by_id(db, knowledgeshare_id=knowledgeshare_id, current_user=current_user)
        if not db_knowledgeshare:
            api_logger.warning(f"The knowledge base sharing does not exist or access is denied: knowledgeshare_id={knowledgeshare_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base sharing does not exist or access is denied"
            )

        api_logger.info(f"Knowledge base sharing query successful: (ID: {db_knowledgeshare.id})")
        return success(data=knowledgeshare_schema.KnowledgeShare.model_validate(db_knowledgeshare), msg="Successfully obtained knowledge base sharing information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Knowledge base sharing query failed: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


@router.delete("/{knowledgeshare_id}", response_model=ApiResponse)
async def delete_knowledgeshare(
        knowledgeshare_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Delete knowledge base sharing
    """
    api_logger.info(f"Delete knowledge base sharing request: knowledgeshare_id={knowledgeshare_id}, username: {current_user.username}")

    try:
        # 1. Query knowledge base sharing information from the database
        api_logger.debug(f"Query knowledge base sharing: {knowledgeshare_id}")
        db_knowledgeshare = knowledgeshare_service.get_knowledgeshare_by_id(db, knowledgeshare_id=knowledgeshare_id, current_user=current_user)
        if not db_knowledgeshare:
            api_logger.warning(f"The knowledge base sharing does not exist or access is denied: knowledgeshare_id={knowledgeshare_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base sharing does not exist or access is denied"
            )
        # 2. Deleting shared knowledge base
        knowledge_service.delete_knowledge_by_id(db, knowledge_id=db_knowledgeshare.target_kb_id ,current_user=current_user)
        # 3. Delete knowledge base sharing
        api_logger.debug(f"perform knowledge base sharing delete: (ID: {knowledgeshare_id})")

        knowledgeshare_service.delete_knowledgeshare_by_id(db, knowledgeshare_id=knowledgeshare_id, current_user=current_user)
        api_logger.info(f"The knowledge base sharing has been successfully deleted: (ID: {knowledgeshare_id})")
        return success(msg="The knowledge base sharing has been successfully deleted")
    except Exception as e:
        api_logger.error(f"Failed to delete from the knowledge base sharing: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise
