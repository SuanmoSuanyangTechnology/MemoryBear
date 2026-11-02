"""Shared public-chat context resolution.

Both the ``/v1`` API chat and the public share chat resolve the viewer's end
user and the workspace memory context; the helpers live here so the chat use
case never imports from a routes module.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Workspace
from ..quota.alerts import report_quota_change
from ..quota.usage import check_end_user_quota_async
from ..repositories import knowledge_repository
from ..repositories.end_user_repository import EndUserRepository
from ..utils.redis_cache import CACHE_MISS, get_json_async, redis_cache, set_json_async
from . import workspace_service

logger = logging.getLogger(__name__)


@redis_cache(ttl=120, prefix="storage_type", skip_args=["db"], id_arg="workspace_id")
async def _prepare_public_memory_context_async(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> tuple[str, str]:
    """Resolve (storage_type, user_rag_memory_id) for a chat request.

    Only RAG workspaces hit the knowledge store; a missing USER_RAG_MERORY
    knowledge base warns and degrades the workspace to neo4j storage.
    """

    storage_type = await workspace_service.get_workspace_storage_type_without_auth_async(
        db=db,
        workspace_id=workspace_id,
    )
    if storage_type is None:
        storage_type = "neo4j"

    user_rag_memory_id = ""
    if storage_type == "rag":
        knowledge = await knowledge_repository.get_knowledge_by_name_async(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id,
        )
        if knowledge:
            user_rag_memory_id = str(knowledge.id)
        else:
            logger.warning(
                f"未找到名为 'USER_RAG_MERORY' 的知识库，"
                f"workspace_id: {workspace_id}，将使用 neo4j 存储"
            )
            storage_type = "neo4j"

    return storage_type, user_rag_memory_id


async def _get_or_create_public_end_user_async(
    db: AsyncSession,
    *,
    app_id: uuid.UUID,
    workspace_id: uuid.UUID,
    other_id: str,
    original_user_id: str | None = None,
):
    """Resolve the chat user's end user, creating it on first contact.

    Cached by (workspace, other_id); a fresh user runs the end-user admission
    check and the post-create alert evaluation.
    """

    end_user_repo = EndUserRepository(db)

    cache_key = f"cache:v2:end_user:{workspace_id}:{other_id}"
    cached = await get_json_async(cache_key)
    if cached is not CACHE_MISS:
        cached_id = cached.get("id")
        if cached_id and cached.get("app_id") == str(app_id):
            return SimpleNamespace(id=uuid.UUID(cached_id), app_id=app_id)

    existing_end_user = await end_user_repo.get_end_user_by_other_id_async(
        workspace_id=workspace_id,
        other_id=other_id,
    )
    if existing_end_user is not None:
        if existing_end_user.app_id != app_id:
            existing_end_user.app_id = app_id
            await db.commit()
        await set_json_async(cache_key, {
            "id": str(existing_end_user.id),
            "app_id": str(existing_end_user.app_id),
        }, ttl=120)
        return existing_end_user

    workspace = await db.get(Workspace, workspace_id)
    # The quota check may roll back this AsyncSession (premium tables missing)
    # and expire ORM instances; save the scalar first and never touch
    # workspace.* afterwards to avoid a MissingGreenlet on attribute refresh.
    workspace_tenant_id = workspace.tenant_id if workspace is not None else None
    if workspace_tenant_id is not None:
        await check_end_user_quota_async(
            db,
            workspace_tenant_id,
            workspace_id=workspace_id,
        )

    new_user = await end_user_repo.get_or_create_end_user_async(
        app_id=app_id,
        workspace_id=workspace_id,
        other_id=other_id,
        original_user_id=original_user_id,
    )
    # The end user is persisted and usage actually changed: evaluate alerts now.
    if workspace_tenant_id is not None:
        await report_quota_change(
            workspace_tenant_id,
            "end_user_quota",
            workspace_id=workspace_id,
        )
    await set_json_async(cache_key, {
        "id": str(new_user.id),
        "app_id": str(new_user.app_id),
    }, ttl=120)
    return new_user


__all__ = [
    "_get_or_create_public_end_user_async",
    "_prepare_public_memory_context_async",
]
