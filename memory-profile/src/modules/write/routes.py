"""Internal routes for the ``/memory`` endpoints this service serves.

The host maps its own ``/api`` (JWT face) and ``/v1`` (API-key face) paths onto
these internal paths; one route serves both faces because the principal carries
the face. ``POST /memory/write`` is the API-key write entry point — the API's
own route forwards its raw body here, and the response envelope is the host's.

``POST /memory/ingest-workflow`` and ``POST /memory/ingest-agent`` are the
application-service callbacks that used to reach the monolith directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from src.common.language_utils import get_language_from_header
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.write.handler import MemoryHandler
from src.modules.write.schemas import (
    IngestAgentInput,
    IngestWorkflowInput,
    MergeUserInput,
    WriteApiResponse,
    WriteInput,
)

router = APIRouter(prefix="/memory", tags=["Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> MemoryHandler:
    return MemoryHandler(request.app.state.runtime)


@router.post("/write", response_model=None)
async def write_memory(
        request: Request,
        context: Context,
        payload: WriteInput,
        language_type: Annotated[str | None, Header(alias="X-Language-Type")] = None,
) -> WriteApiResponse | JSONResponse:
    """Persist one API-key batch and dispatch the API worker's write tasks."""

    return await _handler(request).write(
        context,
        payload.model_dump(mode="json"),
        get_language_from_header(language_type),
    )


@router.post("/ingest-workflow", response_model=None)
async def ingest_workflow(
        request: Request,
        context: Context,
        payload: IngestWorkflowInput,
) -> WriteApiResponse | JSONResponse:
    """Application-service callback: workflow node message ingestion."""

    return await _handler(request).ingest_workflow(
        context, payload.model_dump(mode="json")
    )


@router.post("/ingest-agent", response_model=None)
async def ingest_agent(
        request: Request,
        context: Context,
        payload: IngestAgentInput,
) -> WriteApiResponse | JSONResponse:
    """Application-service callback: one agent turn (user + assistant)."""

    return await _handler(request).ingest_agent(
        context, payload.model_dump(mode="json")
    )


@router.post("/merge", response_model=None)
async def merge_memory(
        request: Request,
        context: Context,
        payload: MergeUserInput,
) -> WriteApiResponse | JSONResponse:
    return await _handler(request).merge(
        context, payload.model_dump(mode="json")
    )


@router.get("")
async def get_memory_info(request: Request, context: Context):
    del context
    return _handler(request).memory_info()
