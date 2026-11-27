"""JWT management endpoint; not exposed on the API-key face."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.reflection.handler import ReflectionHandler
from src.schemas.reflection_trial import ReflectionTrialData, ReflectionTrialFailure

router = APIRouter(prefix="/memory/reflection", tags=["Memory Reflection"])


@router.get("/run", response_model=ApiResponse[ReflectionTrialData | ReflectionTrialFailure])
async def reflection_run(
    request: Request,
    context: Annotated[MemoryPrincipal, Depends(require_memory_context)],
    config_id: UUID | int,
) -> ApiResponse[ReflectionTrialData | ReflectionTrialFailure] | JSONResponse:
    return await ReflectionHandler(request.app.state.runtime).run(context, config_id, request)
