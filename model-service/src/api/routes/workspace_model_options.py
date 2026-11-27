"""Workspace model options (``/internal/v1/workspace-model-options``).

The identity service assembles the public workspace-model responses from these
endpoints: the six-slot catalog for ``GET /model_options`` and id rendering for
``GET /default_models``. Both run on the request-scoped sync session
(management-plane sync decision), so the handlers are plain ``def`` and FastAPI
runs them in the thread pool.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...services import workspace_model_options as options_service
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]

router = APIRouter(
    prefix="/workspace-model-options",
    tags=["Workspace Model Options"],
)


class WorkspaceModelOptionItemsRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=32)


@router.get("", response_model=ApiResponse)
def list_workspace_model_options(db: DbSession, principal: Caller):
    """Six-slot catalog of the caller tenant's accessible workspace models."""

    data = options_service.get_workspace_model_options(
        db, tenant_id=principal.tenant_id
    )
    return success(data=data)


@router.post("/items", response_model=ApiResponse)
def list_workspace_model_option_items(
    body: WorkspaceModelOptionItemsRequest, db: DbSession, principal: Caller
):
    """Render requested model-config ids; misses are returned as absent."""

    items = options_service.get_workspace_model_option_items(
        db, tenant_id=principal.tenant_id, raw_ids=body.ids
    )
    return success(data={"items": items})
