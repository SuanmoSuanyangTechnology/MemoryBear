"""Workspace model-selection validation (``/internal/v1/workspace-model-selection``).

The identity service is the sole caller: the host's workspace create/update
gate no longer reads ``model_configs`` directly; it posts the slot selection
and renders the returned issues with its own locale.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...schemas.workspace_model_selection_schema import (
    WorkspaceModelSelectionRequest,
)
from ...services.workspace_model_selection import validate_selection
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]

router = APIRouter(
    prefix="/workspace-model-selection",
    tags=["Workspace Model Selection"],
)


@router.post("/validate", response_model=ApiResponse)
async def validate_workspace_model_selection(
    body: WorkspaceModelSelectionRequest,
    db: DbSession,
    principal: Caller,
):
    """Run the workspace model-selection static gate or live probes."""

    result = await validate_selection(
        db,
        tenant_id=principal.tenant_id,
        selection=body.selection,
        static_check=body.static_check,
        probe_slots=body.probe_slots,
    )
    return success(data=result)
