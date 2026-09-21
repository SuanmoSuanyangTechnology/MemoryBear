from typing import Optional
import uuid
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_async_db
from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.models.user_model import User
from app.schemas import knowledgeshare_schema
from app.schemas.response_schema import ApiResponse
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.integrations.knowledge.contracts import KnowledgeContextError
router = APIRouter(prefix='/knowledgeshares', tags=['knowledgeshares'], dependencies=[Depends(get_current_user_async)])

@router.get('/{kb_id}/knowledgeshares', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_knowledgeshares(kb_id: uuid.UUID, page: int=Query(1, gt=0), pagesize: int=Query(20, gt=0, le=100), orderby: Optional[str]=Query(None, description='Sort fields, such as: created_at,updated_at'), desc: Optional[bool]=Query(False, description='Is it descending order'), db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Paged query knowledge base sharing list
    - Support filtering by kb_id
    - Support dynamic sorting
    - Return paging metadata + share list
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.post('/knowledgeshare', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_knowledgeshare(create_data: knowledgeshare_schema.KnowledgeShareCreate, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    create knowledgeshare
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.get('/{knowledgeshare_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_knowledgeshare(knowledgeshare_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Retrieve knowledge base sharing information based on knowledgeshare_id
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')

@router.delete('/{knowledgeshare_id}', response_model=ApiResponse)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_knowledgeshare(knowledgeshare_id: uuid.UUID, db: AsyncSession=Depends(get_async_db), current_user: User=Depends(get_current_user_async), request: Request=None):
    """
    Delete knowledge base sharing
    """
    raise KnowledgeContextError('Knowledge route requires the remote service proxy')
