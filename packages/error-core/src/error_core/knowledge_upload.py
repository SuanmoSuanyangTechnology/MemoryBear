"""Wire contracts shared by the versioned knowledge upload entrypoints."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .codes import HTTP_STATUS_TO_BIZ_CODE, BizCode
from .diagnostics import format_diagnostics
from .envelope import fail
from .i18n import resolve_locale
from .messages import fallback_message, user_message

FILE_UPLOAD_PATH = "/files/v1/file"
INTERNAL_UPLOAD_PREFIX = "/internal/v2"


class UploadKind(StrEnum):
    DOCUMENT = "/files/file"
    QA = "/chunks/{kb_id}/import_qa"


def is_versioned_upload(request: Request, *, internal: bool = False) -> bool:
    if request.method != "POST":
        return False
    path = request.url.path
    prefixes = (INTERNAL_UPLOAD_PREFIX,) if internal else ("/api", "/v1")
    path = path.removesuffix("/")
    for prefix in prefixes:
        if not path.startswith(prefix + "/"):
            continue
        parts = path.removeprefix(prefix).split("/")
        document_path = UploadKind.DOCUMENT if internal else FILE_UPLOAD_PATH
        return path.removeprefix(prefix) == document_path or (
            len(parts) == (4 if internal else 5)
            and parts[1:-2] == (["chunks"] if internal else ["chunks", "v1"])
            and bool(parts[-2])
            and parts[-1] == "import_qa"
        )
    return False


def upload_locale(request: Request) -> str:
    return resolve_locale(
        explicit=request.query_params.get("lang"),
        language_type=request.headers.get("X-Language-Type"),
        accepted=request.headers.get("Accept-Language"),
    )


class UploadedDocument(BaseModel):
    id: UUID
    kb_id: UUID
    created_by: UUID | None = None
    file_id: UUID
    file_name: str
    file_ext: str
    file_size: int
    file_meta: dict[str, Any]
    parser_id: str
    parser_config: dict[str, Any]
    parent_child_mode: bool
    chunk_num: int
    progress: float
    progress_msg: str | None
    process_begin_at: int
    process_duration: float
    run: int
    status: int
    created_at: int
    updated_at: int


class UploadedQA(BaseModel):
    file_id: UUID
    document_id: UUID
    task_id: str


class UploadCreated[ResultT](BaseModel):
    code: Literal[0]
    msg: str
    data: ResultT
    error: Literal[""]
    time: int


class UploadFailure(BaseModel):
    code: int
    msg: str
    data: dict[str, Any]
    error: str
    time: int


class DuplicateFileData(BaseModel):
    existing_file_name: str


class DuplicateUpload(UploadFailure):
    code: Literal[9244]
    data: DuplicateFileData


def upload_responses(
    *, qa: bool = False, outer_status: int | None = None,
) -> dict[int, dict[str, Any]]:
    responses = {
        201: {"description": "文档已创建，QA 导入任务已提交" if qa else "文档已创建"},
        400: {"description": "请求格式无法解析"},
        401: {"description": "未认证"},
        403: {"description": "无权限"},
        404: {"description": "目标知识库或目录不存在或不可见"},
        409: {"description": "同一知识库内存在相同文件", "model": DuplicateUpload},
        413: {"description": "文件超限"},
        422: {"description": "参数或文件内容校验失败"},
        500: {"description": "服务内部错误"},
        503: {"description": "必要依赖暂时不可用"},
    }
    if outer_status == 402:
        responses[402] = {"description": "知识库容量配额不足"}
    elif outer_status == 429:
        responses[429] = {"description": "API Key 触发限流或调用配额"}
    if qa:
        responses[415] = {"description": "不支持的 QA 文件格式"}
    for status, definition in responses.items():
        if status != 201:
            definition.setdefault("model", UploadFailure)
        definition["headers"] = {
            "X-Trace-Id": {"description": "请求追踪 ID", "schema": {"type": "string"}},
        }
    return responses


def upload_error_response(
    request: Request,
    status: int,
    *,
    cause: str,
    trace_id: str,
    code: int | None = None,
    data: dict[str, str] | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    if code is None:
        if status == 413:
            code = BizCode.FILE_TOO_LARGE
        elif status == 415:
            code = BizCode.PARSER_NOT_SUPPORTED
        else:
            fallback = BizCode.INTERNAL_ERROR if status >= 500 else BizCode.BAD_REQUEST
            code = HTTP_STATUS_TO_BIZ_CODE.get(status, fallback)
    locale = upload_locale(request)
    detail = f"kb_cause={cause}"
    if validation_errors:
        fields = (
            f"{'.'.join(map(str, error['loc']))}: {error['type']}"
            for error in validation_errors
        )
        detail += "; " + "; ".join(fields)
    response = JSONResponse(
        status_code=status,
        headers=headers,
        content=fail(
            code=code,
            msg=user_message(code, locale) or fallback_message(status, locale),
            error=format_diagnostics(trace_id=trace_id, detail=detail),
            data=data,
        ),
    )
    response.headers["X-Trace-Id"] = trace_id
    response.headers["Content-Language"] = locale
    return response
