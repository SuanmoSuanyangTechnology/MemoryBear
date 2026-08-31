"""Build detached knowledge call contexts for internal application calls."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import get_async_db_context
from app.models.app_model import App
from app.models.user_model import User
from app.models.workspace_model import Workspace

from .contracts import (
    KnowledgeCallContext,
    KnowledgeContextError,
    KnowledgePrincipal,
    KnowledgeRetrievalSource,
)


async def build_app_knowledge_context(
    app_id: uuid.UUID | str | None,
    *,
    source: KnowledgeRetrievalSource,
    trace_id: str,
    expected_workspace_id: uuid.UUID | str | None = None,
) -> KnowledgeCallContext:
    """Resolve the configuration owner and close DB before retrieval I/O."""

    try:
        normalized_app_id = uuid.UUID(str(app_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise KnowledgeContextError("Knowledge app ID is invalid") from exc

    async with get_async_db_context() as db:
        result = await db.execute(
            select(
                App.created_by,
                User.username,
                User.tenant_id.label("user_tenant_id"),
                App.workspace_id,
                Workspace.tenant_id.label("workspace_tenant_id"),
            )
            .join(User, User.id == App.created_by)
            .join(Workspace, Workspace.id == App.workspace_id)
            .where(App.id == normalized_app_id)
        )
        snapshot = result.one_or_none()

    if snapshot is None:
        raise KnowledgeContextError("Knowledge app owner context is unavailable")
    if snapshot.user_tenant_id != snapshot.workspace_tenant_id:
        raise KnowledgeContextError("Knowledge app owner tenant does not match workspace")
    if expected_workspace_id is not None:
        try:
            normalized_workspace_id = uuid.UUID(str(expected_workspace_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise KnowledgeContextError("Knowledge workspace ID is invalid") from exc
        if normalized_workspace_id != snapshot.workspace_id:
            raise KnowledgeContextError("Knowledge app workspace does not match runtime")

    return KnowledgeCallContext(
        principal=KnowledgePrincipal(
            actor_id=snapshot.created_by,
            actor_name=snapshot.username,
            tenant_id=snapshot.workspace_tenant_id,
            workspace_id=snapshot.workspace_id,
        ),
        source=source,
        trace_id=trace_id,
    )
