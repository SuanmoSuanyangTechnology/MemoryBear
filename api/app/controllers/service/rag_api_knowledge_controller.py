"""RAG 服务接口 - 基于 API Key 认证"""

from typing import Optional, Dict
import uuid

from fastapi import APIRouter, Body, Depends, Request, Query
from sqlalchemy.orm import Session

from app.controllers import knowledge_controller
from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.models import knowledge_model
from app.schemas import knowledge_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services import api_key_service


router = APIRouter(prefix="/knowledges", tags=["V1 - RAG API"])
api_logger = get_business_logger()


@router.get("/knowledgetype", response_model=ApiResponse)
def get_knowledge_types():
    return success(msg="Successfully obtained the knowledge type", data=list(knowledge_model.KnowledgeType))


@router.get("/permissiontype", response_model=ApiResponse)
def get_permission_types():
    return success(msg="Successfully obtained the knowledge permission type", data=list(knowledge_model.PermissionType))


@router.get("/parsertype", response_model=ApiResponse)
def get_parser_types():
    return success(msg="Successfully obtained the knowledge parser type", data=list(knowledge_model.ParserType))


@router.get("/knowledge_graph_entity_types", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_knowledge_graph_entity_types(
    llm_id: uuid.UUID,
    scenario: str,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    get knowledge graph entity types based on llm_id
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.get_knowledge_graph_entity_types(llm_id=llm_id,
                                                                       scenario=scenario,
                                                                       db=db,
                                                                       current_user=current_user)


@router.get("/knowledges", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_knowledges(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    parent_id: Optional[uuid.UUID] = Query(None, description="parent folder id"),
    page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
    pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
    orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at,updated_at"),
    desc: Optional[bool] = Query(False, description="Is it descending order"),
    keywords: Optional[str] = Query(None, description="Search keywords (knowledge base name)"),
    kb_ids: Optional[str] = Query(None, description="Knowledge base ids, separated by commas")
):
    """
    Query the knowledge base list in pages
    - Support filtering by parent_id
    -  Support keyword search for knowledge base names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.get_knowledges(parent_id=parent_id,
                                                     page=page,
                                                     pagesize=pagesize,
                                                     orderby=orderby,
                                                     desc=desc,
                                                     keywords=keywords,
                                                     kb_ids=kb_ids,
                                                     db=db,
                                                     current_user=current_user)


@router.post("/knowledge", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def create_knowledge(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    name: str = Body(..., description="KB name"),
):
    """
    create knowledge
    """
    body = await request.json()
    create_data = knowledge_schema.KnowledgeCreate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.create_knowledge(create_data=create_data,
                                                       db=db,
                                                       current_user=current_user)


@router.get("/{knowledge_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_knowledge(
    knowledge_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Retrieve knowledge base information based on knowledge_id
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.get_knowledge(knowledge_id=knowledge_id,
                                                    db=db,
                                                    current_user=current_user)


@router.put("/{knowledge_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def update_knowledge(
    knowledge_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    name: str = Body(None, description="KB name (optional)"),
):
    body = await request.json()
    update_data = knowledge_schema.KnowledgeUpdate(**body)
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.update_knowledge(knowledge_id=knowledge_id,
                                                       update_data=update_data,
                                                       db=db,
                                                       current_user=current_user)


@router.delete("/{knowledge_id}", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def delete_knowledge(
    knowledge_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Soft-delete knowledge base
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.delete_knowledge(knowledge_id=knowledge_id,
                                                       db=db,
                                                       current_user=current_user)


@router.get("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def get_knowledge_graph(
    knowledge_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Retrieve knowledge_graph base information based on knowledge_id
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.get_knowledge_graph(knowledge_id=knowledge_id,
                                                          db=db,
                                                          current_user=current_user)


@router.delete("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def delete_knowledge_graph(
    knowledge_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    delete knowledge graph
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.delete_knowledge_graph(knowledge_id=knowledge_id,
                                                             db=db,
                                                             current_user=current_user)


@router.post("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
@require_api_key(scopes=["rag"])
async def rebuild_knowledge_graph(
    knowledge_id: uuid.UUID,
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    rebuild knowledge graph
    """
    # 0. Obtain the creator of the api key
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    return await knowledge_controller.rebuild_knowledge_graph(knowledge_id=knowledge_id,
                                                              db=db,
                                                              current_user=current_user)

