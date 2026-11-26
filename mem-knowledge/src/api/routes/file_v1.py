"""Single-file upload with an independent saved configuration and dispatch result."""

import json
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, UploadFile
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import FormData, Headers
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import JSONResponse

from ...errors import KnowledgeError
from ...runtime import ProcessRuntime
from ...services import document_v1 as service
from ...services.document import ParseDocumentSnapshot
from ...services.knowledge_file_storage import KnowledgeFileStorage
from ...services.upload_deduplication import save_new_upload_result
from ..dependencies import Principal, get_principal, get_runtime, get_source
from ..error_handlers import render_error, request_locale
from ..knowledge_capacity import check_workspace_capacity
from ..schemas.common import SuccessEnvelope, success
from ..schemas.document_v1 import UploadAndParseData
from .knowledge_v1 import check_query
from .upload_v2 import read_upload

router = APIRouter(prefix="/files/v1", tags=["file-v1"], dependencies=[Depends(get_source)])


class ConfigurationMultipartParser(MultiPartParser):
    """Check the JSON part header before Starlette discards headers of text fields."""

    async def parse(self) -> FormData:
        try:
            return await super().parse()
        except MultipartParseError:
            for temporary_file in self._files_to_close_on_error:
                temporary_file.close()
            raise MultiPartException("Invalid multipart body") from None

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        if self._current_part.field_name == "processing_configuration":
            content_type = Headers(raw=self._current_part.item_headers).get("content-type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise MultiPartException("Configuration requires application/json")


@router.post(
    "/upload-and-parse",
    status_code=201,
    response_model=SuccessEnvelope[UploadAndParseData],
    response_model_exclude_none=True,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "additionalProperties": False,
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "processing_configuration": {"type": "object"},
                        },
                    },
                    "encoding": {"processing_configuration": {"contentType": "application/json"}},
                }
            },
        }
    },
)
async def upload_and_parse(
    request: Request,
    kb_id: UUID,
    parent_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict[str, Any] | JSONResponse:
    check_query(request, {"kb_id", "parent_id"})
    await check_workspace_capacity(request, runtime, principal)
    invalid = KnowledgeError.from_code(
        "KB_VALIDATION_ERROR",
        status_code=422,
        diagnostic="body: invalid multipart upload",
    )
    if request.headers.get("content-type", "").split(";", 1)[0] != "multipart/form-data":
        raise invalid
    try:
        form = await ConfigurationMultipartParser(
            request.headers,
            request.stream(),
            max_files=2,
            max_fields=1,
        ).parse()
    except MultiPartException:
        raise invalid from None
    try:
        if set(form) - {"file", "processing_configuration"} or len(form.getlist("file")) != 1:
            raise invalid
        if len(form.getlist("processing_configuration")) > 1:
            raise invalid
        file = form["file"]
        if not isinstance(file, StarletteUploadFile):
            raise invalid
        raw = form.get("processing_configuration", "{}")
        if isinstance(raw, StarletteUploadFile):
            raw = await raw.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise invalid
        try:
            configuration = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise invalid from None
        if not isinstance(configuration, dict):
            raise invalid
        content = await read_upload(cast(UploadFile, file), runtime)
        async with runtime.database.async_session() as db:
            plan = await service.prepare_upload(
                db,
                kb_id,
                parent_id,
                file.filename or "",
                content,
                configuration,
                principal,
                runtime,
            )
        result = await save_new_upload_result(
            runtime,
            KnowledgeFileStorage(runtime.storage),
            plan,
            principal,
            content,
            file.content_type,
        )
        if result.state == "commit_unknown":
            return render_error(
                request,
                KnowledgeError.from_code("KB_UPLOAD_SAVE_UNKNOWN"),
                data={
                    "file_id": str(result.file_id),
                    "document_id": str(result.document_id) if result.document_id else None,
                    "file_saved": None,
                },
            )
        # A session-close error after an acknowledged COMMIT cannot undo a saved file.
        outcome = result.outcome if result.committed else result.unwrap()
        assert outcome is not None
    finally:
        await form.close()
    snapshot = ParseDocumentSnapshot(
        outcome.document_id,
        plan.file_key,
        plan.file_name,
        kb_id if plan.configuration.doc_type == "qa" else None,
    )
    return success(
        {
            "file_id": str(outcome.file_id),
            "document_id": str(outcome.document_id),
            "file_saved": True,
            "processing": await service.dispatch_processing(
                runtime, snapshot, request_locale(request)
            ),
        }
    )
