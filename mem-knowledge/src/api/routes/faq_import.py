"""管理端 FAQ 异步导入入口。"""

import asyncio
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from ...errors import KnowledgeError
from ...runtime import ProcessRuntime
from ...services import wiki_faq_import as service
from ..dependencies import Principal, get_runtime
from ..schemas.common import SuccessEnvelope, success
from .wiki_faq import manager_principal, scope_for

router = APIRouter(prefix="/knowledges/{kb_id}/faqs/import", tags=["wiki-faq"])
JSON_ENVELOPE_BYTES = 1024 * 1024


class FAQImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    standard_question: StrictStr
    standard_answer: StrictStr
    source_position: int = Field(ge=1, strict=True)


class FAQImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["append", "replace"]
    items: list[FAQImportItem] = Field(min_length=1)


async def read_payload(request: Request, max_bytes: int) -> FAQImportRequest:
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise KnowledgeError.from_code("KB_QA_IMPORT_FILE_TYPE_UNSUPPORTED", status_code=415)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise KnowledgeError.from_code("KB_HTTP_TOO_LARGE", status_code=413)
        body.extend(chunk)
    try:
        value = await asyncio.to_thread(json.loads, body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", status_code=400) from exc
    try:
        return await asyncio.to_thread(FAQImportRequest.model_validate, value)
    except ValidationError as exc:
        from fastapi.exceptions import RequestValidationError

        raise RequestValidationError(
            exc.errors(include_input=False, include_context=False)
        ) from exc


@router.post(
    "",
    status_code=202,
    response_model=SuccessEnvelope[dict],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["mode", "items"],
                        "properties": {
                            "mode": {"type": "string", "enum": ["append", "replace"]},
                            "items": {
                                "type": "array",
                                "minItems": 1,
                                "items": FAQImportItem.model_json_schema(),
                            },
                        },
                    },
                }
            },
        }
    },
)
async def submit_import(
    request: Request,
    kb_id: UUID,
    principal: Annotated[Principal, Depends(manager_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
):
    payload = await read_payload(request, runtime.settings.max_file_size + JSON_ENVELOPE_BYTES)
    return success(
        await service.submit(runtime, scope_for(kb_id, principal), principal.actor_id, payload)
    )


@router.get("/{task_id}", response_model=SuccessEnvelope[dict])
async def import_progress(
    kb_id: UUID,
    task_id: UUID,
    principal: Annotated[Principal, Depends(manager_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
):
    return success(await service.progress(runtime, scope_for(kb_id, principal), task_id))
