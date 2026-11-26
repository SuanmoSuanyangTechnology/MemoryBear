"""V1 document maintenance and explicit processing requests."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from ...errors import KnowledgeError
from ...runtime import ProcessRuntime
from ...services import document_v1 as service
from ..dependencies import Principal, get_principal, get_runtime, get_source
from ..error_handlers import request_locale
from ..schemas.common import SuccessEnvelope, success
from ..schemas.document_v1 import RechunkData
from .knowledge_v1 import check_query

router = APIRouter(
    prefix="/documents/v1",
    tags=["document-v1"],
    dependencies=[Depends(get_source)],
)


@router.get("/{document_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def get_document(
    request: Request,
    document_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    check_query(request, set())
    async with runtime.database.async_session() as db:
        return success(
            service.document_to_data(
                await service.get_document(db, document_id, principal),
            )
        )


@router.post(
    "/{document_id}/rechunk",
    response_model=SuccessEnvelope[RechunkData],
    response_model_exclude_none=True,
)
async def rechunk_document(
    request: Request,
    document_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    check_query(request, set())
    async for chunk in request.stream():
        if chunk:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                status_code=422,
                diagnostic="body: not permitted",
            )
    async with runtime.database.async_session() as db:
        snapshot = await service.prepare_rechunk(db, document_id, principal)
    return success(
        {
            "document_id": str(document_id),
            "processing": await service.dispatch_processing(
                runtime, snapshot, request_locale(request)
            ),
        }
    )
