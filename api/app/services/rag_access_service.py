import uuid
from types import ModuleType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.rag.retrieval.models import RetrievalPrincipal
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot, make_snapshot
from app.integrations.knowledge.contracts import (
    KnowledgeCallContext,
    KnowledgeContextError,
    KnowledgePrincipal,
    KnowledgeRetrievalSource,
)
from app.models.api_key_model import ApiKey
from app.models import document_model, knowledge_model
from app.models.user_model import User
from app.models.workspace_model import Workspace, WorkspaceMember
from app.repositories import workspace_repository
from app.schemas.api_key_schema import ApiKeyAuth
from app.services.knowledge_retrieval_service import KnowledgeRetrievalAccessDenied


class _CurrentWorkspaceGuardBypass:
    def __init__(self, controller: ModuleType):
        self._controller = controller

    def __getattr__(self, name: str) -> Any:
        handler = getattr(self._controller, name)
        return getattr(handler, "__wrapped__", handler)


def unwrap_current_workspace_guard(controller: ModuleType) -> _CurrentWorkspaceGuardBypass:
    """Expose controller handlers for API Key calls after API Key scope has been verified."""
    return _CurrentWorkspaceGuardBypass(controller)


def get_api_key_request_user(
    api_key: ApiKey,
    api_key_auth: ApiKeyAuth,
) -> CurrentUserSnapshot:
    """Build a request-local user snapshot scoped to the API Key workspace."""
    return make_snapshot(api_key.creator, api_key_auth.workspace_id)


async def get_api_key_retrieval_principal_async(
    api_key_auth: ApiKeyAuth,
) -> RetrievalPrincipal:
    """Read the API Key creator into a retrieval-safe principal snapshot."""

    async with get_async_db_context() as db:
        result = await db.execute(
            select(User.id, User.username, User.tenant_id, User.is_superuser)
            .join(ApiKey, ApiKey.created_by == User.id)
            .where(
                ApiKey.id == api_key_auth.api_key_id,
                ApiKey.workspace_id == api_key_auth.workspace_id,
            )
        )
        creator = result.one_or_none()

    if creator is None:
        raise KnowledgeRetrievalAccessDenied("API Key creator is unavailable")

    return RetrievalPrincipal(
        id=creator.id,
        username=creator.username,
        tenant_id=creator.tenant_id,
        current_workspace_id=api_key_auth.workspace_id,
        is_superuser=bool(creator.is_superuser),
    )


async def get_api_key_knowledge_context(
    api_key_auth: ApiKeyAuth,
    *,
    source: KnowledgeRetrievalSource,
    trace_id: str,
) -> KnowledgeCallContext:
    """Load an API Key creator snapshot without holding DB during remote I/O."""

    async with get_async_db_context() as db:
        result = await db.execute(
            select(User.id, User.username, User.tenant_id)
            .join(ApiKey, ApiKey.created_by == User.id)
            .where(
                ApiKey.id == api_key_auth.api_key_id,
                ApiKey.workspace_id == api_key_auth.workspace_id,
            )
        )
        creator = result.one_or_none()

    if creator is None:
        raise KnowledgeContextError("API Key creator is unavailable")

    return KnowledgeCallContext(
        principal=KnowledgePrincipal(
            actor_id=creator.id,
            actor_name=creator.username,
            tenant_id=creator.tenant_id,
            workspace_id=api_key_auth.workspace_id,
        ),
        source=source,
        trace_id=trace_id,
    )


def has_current_workspace_access(
    db: Session,
    current_user: User,
) -> bool:
    """检查当前用户是否可访问自己的 current workspace。"""
    if not current_user.current_workspace_id:
        return False

    workspace = workspace_repository.get_workspace_by_id(
        db=db,
        workspace_id=current_user.current_workspace_id,
    )
    if not workspace:
        return False

    if current_user.is_superuser:
        return current_user.tenant_id == workspace.tenant_id

    member = workspace_repository.get_member_in_workspace(
        db=db,
        user_id=current_user.id,
        workspace_id=current_user.current_workspace_id,
    )
    return member is not None


async def has_current_workspace_access_async(
    db: AsyncSession,
    current_user: User,
) -> bool:
    """Async version of has_current_workspace_access."""
    if not current_user.current_workspace_id:
        return False

    workspace = await db.get(Workspace, current_user.current_workspace_id)
    if not workspace:
        return False

    if current_user.is_superuser:
        return current_user.tenant_id == workspace.tenant_id

    result = await db.execute(
        select(WorkspaceMember.id).where(
            WorkspaceMember.user_id == current_user.id,
            WorkspaceMember.workspace_id == current_user.current_workspace_id,
            WorkspaceMember.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


def require_current_workspace_knowledge(
    db: Session,
    knowledge_id: uuid.UUID,
    current_user: User,
):
    """验证知识库存在且属于当前 workspace。"""
    if not has_current_workspace_access(db=db, current_user=current_user):
        return None

    return (
        db.query(knowledge_model.Knowledge)
        .filter(
            knowledge_model.Knowledge.id == knowledge_id,
            knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
        )
        .first()
    )


async def require_current_workspace_knowledge_async(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    current_user: User,
):
    """Async version of require_current_workspace_knowledge."""
    if not await has_current_workspace_access_async(db=db, current_user=current_user):
        return None

    result = await db.execute(
        select(knowledge_model.Knowledge).where(
            knowledge_model.Knowledge.id == knowledge_id,
            knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
        )
    )
    return result.scalars().first()


def require_current_workspace_document(
    db: Session,
    document_id: uuid.UUID,
    current_user: User,
):
    """验证文档存在且所属知识库在当前 workspace。"""
    db_document = db.query(document_model.Document).filter(document_model.Document.id == document_id).first()
    if not db_document:
        return None

    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=db_document.kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        return None

    return db_document


async def require_current_workspace_document_async(
    db: AsyncSession,
    document_id: uuid.UUID,
    current_user: User,
):
    """Async version of require_current_workspace_document."""
    result = await db.execute(
        select(document_model.Document).where(document_model.Document.id == document_id)
    )
    db_document = result.scalars().first()
    if not db_document:
        return None

    db_knowledge = await require_current_workspace_knowledge_async(
        db=db,
        knowledge_id=db_document.kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        return None

    return db_document
