"""Internal routes for workspace temporary-memory retention."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.i18n import get_request_language
from src.middleware.auth import SOURCE_INTERNAL, MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.workspace_retention.handler import WorkspaceRetentionHandler
from src.modules.workspace_retention.schemas import WorkspaceRetentionPolicyUpdate
from src.schemas.workspace import (
    WorkspaceRetentionPolicyResponse,
    WorkspaceRetentionPolicyResult,
)

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


async def _require_user_context(request: Request) -> MemoryPrincipal:
    context = await require_memory_context(request)
    if context.source != SOURCE_INTERNAL or context.actor_kind != "actor":
        raise ProfileError(BizCode.FORBIDDEN, "User authentication required", status_code=403)
    return context


Context = Annotated[MemoryPrincipal, Depends(_require_user_context)]


def _handler(request: Request) -> WorkspaceRetentionHandler:
    return WorkspaceRetentionHandler(request.app.state.runtime)


@router.get(
    "/temporary-memory-retention", response_model=ApiResponse[WorkspaceRetentionPolicyResponse]
)
async def get_workspace_retention_policy(
    request: Request, context: Context
) -> ApiResponse[WorkspaceRetentionPolicyResponse]:
    return await _handler(request).get_workspace_retention_policy(
        context, get_request_language(request)
    )


@router.put(
    "/temporary-memory-retention", response_model=ApiResponse[WorkspaceRetentionPolicyResult]
)
async def update_workspace_retention_policy(
    request: Request, context: Context, policy: WorkspaceRetentionPolicyUpdate
) -> ApiResponse[WorkspaceRetentionPolicyResult]:
    return await _handler(request).update_workspace_retention_policy(
        context, policy, get_request_language(request)
    )
