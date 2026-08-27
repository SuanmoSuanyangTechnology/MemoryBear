"""Internal Knowledge metadata routes migrated from the legacy controller."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ...errors import KnowledgeError
from ...runtime import ProcessRuntime
from ...services import knowledge as knowledge_service
from ...services.knowledge_metadata import KnowledgeMetadataService
from ..dependencies import Principal, get_principal, get_runtime
from ..schemas.common import SuccessEnvelope, success
from ..schemas.knowledge_metadata import (
    BuiltinMetadataEnableRequest,
    KnowledgeMetadataCreate,
    KnowledgeMetadataFieldsRequest,
    KnowledgeMetadataResponse,
    KnowledgeMetadataUpdate,
)

router = APIRouter(
    prefix="/knowledges",
    tags=["knowledge-metadata"],
    dependencies=[Depends(get_principal)],
)


def _success(
    _request: Request,
    data: Any = None,
    msg: str = "OK",
) -> dict[str, Any]:
    return success(data=data, msg=msg)


async def _require_knowledge(
    db,
    knowledge_id: uuid.UUID,
    principal: Principal,
) -> None:
    if await knowledge_service.get_knowledge(db, knowledge_id, principal) is None:
        raise KnowledgeError.from_code(
            "KB_RESOURCE_NOT_FOUND",
            "知识库 不存在",
            status_code=400,
            response_code=4006,
            response_style="business",
        )


def _builtin_fields(fields) -> list[dict[str, Any]]:
    return [
        KnowledgeMetadataResponse(
            id=None,
            type=field.type,
            name=field.name,
            is_builtin=True,
        ).model_dump(mode="json")
        for field in fields
    ]


def _single_fields_result(result: dict) -> dict[str, Any]:
    return {
        "custom": [
            KnowledgeMetadataResponse.model_validate(field).model_dump(mode="json")
            for field in result["custom"]
        ],
        "builtin_enabled": result["builtin_enabled"],
        "builtin_fields": _builtin_fields(result["builtin_fields"]),
    }


@router.post("/metadata/fields", response_model=SuccessEnvelope[dict[str, Any]])
async def list_common_metadata_fields(
    request: Request,
    data: KnowledgeMetadataFieldsRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    kb_ids = list(dict.fromkeys(data.kb_ids))
    async with runtime.database.async_session() as db:
        for kb_id in kb_ids:
            await _require_knowledge(db, kb_id, principal)
        result = await KnowledgeMetadataService.list_metadata_fields_for_knowledge_ids_async(
            db,
            kb_ids,
            include_counts=False,
        )
    formatted = {
        "custom": [
            {
                "type": field["type"],
                "name": field["name"],
                "is_builtin": False,
            }
            for field in result["custom"]
        ],
        "builtin_enabled": result["builtin_enabled"],
        "builtin_fields": [
            {"type": field.type, "name": field.name, "is_builtin": True}
            for field in result["builtin_fields"]
        ],
    }
    return _success(request, formatted)


@router.get("/{kb_id}/metadata", response_model=SuccessEnvelope[dict[str, Any]])
async def list_metadata_fields(
    request: Request,
    kb_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        await _require_knowledge(db, kb_id, principal)
        result = await KnowledgeMetadataService.list_metadata_fields_async(db, kb_id)
    return _success(request, _single_fields_result(result))


@router.post("/{kb_id}/metadata", response_model=SuccessEnvelope[dict[str, Any]])
async def create_metadata_field(
    request: Request,
    kb_id: uuid.UUID,
    data: KnowledgeMetadataCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        await _require_knowledge(db, kb_id, principal)
        field = await KnowledgeMetadataService.create_metadata_field_async(
            db,
            kb_id,
            data.name,
            data.type.value,
            principal.tenant_id,
            principal.actor_id,
        )
    return _success(
        request,
        KnowledgeMetadataResponse.model_validate(field).model_dump(mode="json"),
        "字段创建成功",
    )


@router.put(
    "/{kb_id}/metadata/{metadata_id}",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def update_metadata_field(
    request: Request,
    kb_id: uuid.UUID,
    metadata_id: uuid.UUID,
    data: KnowledgeMetadataUpdate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        await _require_knowledge(db, kb_id, principal)
        field = await KnowledgeMetadataService.update_metadata_field_async(
            db,
            metadata_id,
            kb_id,
            data.name,
            principal.actor_id,
        )
    return _success(
        request,
        KnowledgeMetadataResponse.model_validate(field).model_dump(mode="json"),
        "字段更新成功",
    )


@router.delete("/{kb_id}/metadata/{metadata_id}", response_model=SuccessEnvelope[None])
async def delete_metadata_field(
    request: Request,
    kb_id: uuid.UUID,
    metadata_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[None]:
    async with runtime.database.async_session() as db:
        await _require_knowledge(db, kb_id, principal)
        await KnowledgeMetadataService.delete_metadata_field_async(db, metadata_id, kb_id)
    return _success(request, msg="字段删除成功")


@router.get("/{kb_id}/metadata/builtin", response_model=SuccessEnvelope[dict[str, Any]])
async def get_builtin_metadata_fields(
    request: Request,
    kb_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        await _require_knowledge(db, kb_id, principal)
        result = await KnowledgeMetadataService.get_builtin_fields_async(db, kb_id)
    return _success(
        request,
        {"enabled": result["enabled"], "fields": _builtin_fields(result["fields"])},
    )


@router.post(
    "/{kb_id}/metadata/builtin/enable",
    response_model=SuccessEnvelope[dict[str, bool]],
)
async def toggle_builtin_metadata(
    request: Request,
    kb_id: uuid.UUID,
    data: BuiltinMetadataEnableRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, bool]]:
    async with runtime.database.async_session() as db:
        await _require_knowledge(db, kb_id, principal)
        enabled = await KnowledgeMetadataService.set_builtin_metadata_enabled_async(
            db,
            kb_id,
            data.enabled,
        )
    return _success(
        request,
        {"enabled": enabled},
        "内置元数据开关更新成功",
    )
