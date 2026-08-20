"""Internal KnowledgeShare routes migrated from the legacy controller."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ...runtime import ProcessRuntime
from ...services import knowledge_share as share_service
from ..dependencies import Principal, get_principal, get_runtime
from ..schemas.common import SuccessEnvelope
from ..schemas.knowledge_share import KnowledgeShareCreate

router = APIRouter(
    prefix="/knowledgeshares",
    tags=["knowledgeshares"],
    dependencies=[Depends(get_principal)],
)


def _success(request: Request, data: Any = None) -> SuccessEnvelope[Any]:
    return SuccessEnvelope(data=data, trace_id=request.state.trace_id)


@router.get("/{kb_id}/knowledgeshares", response_model=SuccessEnvelope[dict[str, Any]])
async def get_knowledgeshares(
    request: Request,
    kb_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    page: Annotated[int, Query(gt=0)] = 1,
    pagesize: Annotated[int, Query(gt=0, le=100)] = 20,
    orderby: Annotated[str | None, Query()] = None,
    desc: Annotated[bool, Query()] = False,
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        total, items = await share_service.list_shares(
            db,
            kb_id,
            principal,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
        )
    return _success(
        request,
        {
            "items": items,
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "has_next": page * pagesize < total,
            },
        },
    )


@router.post("/knowledgeshare", response_model=SuccessEnvelope[dict[str, Any]])
async def create_knowledgeshare(
    request: Request,
    create_data: KnowledgeShareCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        data = await share_service.create_share(db, create_data, principal)
    return _success(request, data)


@router.get("/{knowledgeshare_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def get_knowledgeshare(
    request: Request,
    knowledgeshare_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        share = await share_service.get_share(db, knowledgeshare_id, principal)
        if share is None:
            raise share_service._not_found("Knowledge share does not exist")
        data = await share_service.share_to_data(db, share)
    return _success(request, data)


@router.delete("/{knowledgeshare_id}", response_model=SuccessEnvelope[None])
async def delete_knowledgeshare(
    request: Request,
    knowledgeshare_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[None]:
    async with runtime.database.async_session() as db:
        await share_service.delete_share(db, knowledgeshare_id, principal)
    return _success(request)
