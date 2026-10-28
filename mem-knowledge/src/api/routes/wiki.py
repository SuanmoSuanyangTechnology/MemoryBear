"""Management Wiki endpoints for reading published pages and their relationships."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...errors import KnowledgeError
from ...rag.wiki.config import WikiError
from ...repositories.wiki import read
from ...runtime import ProcessRuntime
from ...services.wiki.runtime import transaction
from ..dependencies import Principal, get_principal, get_runtime
from ..schemas.common import SuccessEnvelope, success

router = APIRouter(prefix="/knowledges/{kb_id}/wiki", tags=["wiki"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]
RuntimeDep = Annotated[ProcessRuntime, Depends(get_runtime)]


def _business_error(error):
    codes = {
        "WIKI_NOT_FOUND": "KB_WIKI_NOT_FOUND",
        "WIKI_KNOWLEDGE_UNAVAILABLE": "KB_WIKI_NOT_FOUND",
        "WIKI_DOCUMENT_UNAVAILABLE": "KB_WIKI_NOT_FOUND",
        "WIKI_SCOPE_MISMATCH": "KB_WIKI_NOT_FOUND",
        "WIKI_DISABLED": "KB_WIKI_DISABLED",
        "WIKI_SOURCE_NOT_READY": "KB_WIKI_SOURCE_NOT_READY",
        "WIKI_MODEL_REQUIRED": "KB_WIKI_MODEL_REQUIRED",
    }
    return KnowledgeError.from_code(codes.get(error.code, "KB_CONFLICT"))


def _scope(kb_id, principal):
    return {
        "kb_id": str(kb_id),
        "tenant_id": str(principal.tenant_id),
        "workspace_id": str(principal.workspace_id),
    }


def _types(value):
    values = tuple(part.strip() for part in (value or "").split(",") if part.strip())
    if set(values) - {"summary", "entity", "concept"}:
        raise HTTPException(422, "Unsupported Wiki page type")
    return values


async def _read(runtime, operation, scope, **kwargs):
    try:
        return await asyncio.to_thread(transaction, runtime, operation, scope, **kwargs)
    except WikiError as exc:
        raise _business_error(exc) from None


def _response(request, data):
    return success(data=data, msg="Wiki request completed")


@router.get("/pages", response_model=SuccessEnvelope[dict[str, Any]])
async def list_pages(
    request: Request,
    kb_id: uuid.UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    page_types: str | None = None,
    keyword: str | None = Query(
        None, description="搜索 Wiki 标题、摘要和正文，不区分大小写的字面包含匹配；"
        "忽略首尾空白，空白不过滤，% 和 _ 按普通字符匹配。",
    ),
    page: int = Query(1, ge=1),
    pagesize: int = Query(50, ge=1, le=200),
    limit: int | None = Query(None, ge=1, le=200, include_in_schema=False),
):
    pagesize = pagesize if "pagesize" in request.query_params else limit or pagesize
    return _response(
        request,
        await _read(
            runtime,
            read.list_pages,
            _scope(kb_id, principal),
            keyword=keyword,
            page_types=_types(page_types),
            page=page,
            pagesize=pagesize,
            legacy=limit is not None and "pagesize" not in request.query_params,
        ),
    )


@router.get("/pages/{page_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def page_detail(
    request: Request,
    kb_id: uuid.UUID,
    page_id: uuid.UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    return _response(
        request, await _read(runtime, read.detail, _scope(kb_id, principal), page_id=page_id)
    )


@router.get("/tree", response_model=SuccessEnvelope[dict[str, Any]])
async def tree(
    request: Request,
    kb_id: uuid.UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    folder_id: uuid.UUID | None = None,
    page_types: str | None = None,
    page: int = Query(1, ge=1),
    pagesize: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(
        None, pattern=r"^[01]:[0-9a-fA-F-]{36}$", include_in_schema=False
    ),
    limit: int | None = Query(None, ge=1, le=200, include_in_schema=False),
):
    if cursor:
        try:
            uuid.UUID(cursor[2:])
        except ValueError:
            raise HTTPException(422, "Invalid Wiki cursor") from None
    pagesize = pagesize if "pagesize" in request.query_params else limit or pagesize
    cursor = None if "page" in request.query_params else cursor
    return _response(
        request,
        await _read(
            runtime,
            read.tree,
            _scope(kb_id, principal),
            folder_id=str(folder_id) if folder_id else None,
            page_types=_types(page_types),
            page=page,
            pagesize=pagesize,
            cursor=cursor,
            legacy=(
                limit is not None
                and "page" not in request.query_params
                and "pagesize" not in request.query_params
            ),
        ),
    )


@router.get("/index", response_model=SuccessEnvelope[dict[str, Any]])
async def index(request: Request, kb_id: uuid.UUID, principal: PrincipalDep, runtime: RuntimeDep):
    return _response(request, await _read(runtime, read.index, _scope(kb_id, principal)))


@router.get("/graph", response_model=SuccessEnvelope[dict[str, Any]])
async def graph(
    request: Request,
    kb_id: uuid.UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    mode: Literal["overview", "ego"] = "overview",
    center_page_id: uuid.UUID | None = None,
    types: str | None = None,
    depth: int = Query(1, ge=1, le=2),
    limit: int = Query(500, ge=1, le=5000),
):
    if mode == "ego" and center_page_id is None:
        raise HTTPException(422, "center_page_id is required for ego mode")
    return _response(
        request,
        await _read(
            runtime,
            read.graph,
            _scope(kb_id, principal),
            mode=mode,
            center_page_id=center_page_id,
            page_types=_types(types),
            depth=depth,
            node_limit=min(limit, runtime.settings.kb_wiki_graph_node_limit),
            edge_limit=runtime.settings.kb_wiki_graph_edge_limit,
        ),
    )
