"""MCP market management routes (monolith parity).

Wire-compatible port of the host ``mcp_market_controller``: the list, create,
detail, update and delete endpoints under the host's ``/mcp_markets`` prefix.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...schemas.mcp_market_schema import (
    McpMarket,
    McpMarketCreate,
    McpMarketUpdate,
)
from ...services.mcp_market_service import McpMarketService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/mcp_markets", tags=["mcp_markets"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]

# Host copy kept verbatim; the detail read and the write paths deliberately
# use different texts.
MARKET_NOT_FOUND_ACCESS_MSG = "The mcp market does not exist or access is denied"
MARKET_NOT_FOUND_PERMISSION_MSG = (
    "The mcp market does not exist or you do not have permission to access it"
)
PAGING_ERROR_MSG = "The paging parameter must be greater than 0"


@router.get("/mcp_markets", summary="分页查询 MCP 市场列表", response_model=ApiResponse)
def get_mcp_markets(
    db: DbSession,
    principal: Caller,
    page: int = Query(1, gt=0),
    pagesize: int = Query(20, gt=0, le=100),
    orderby: str | None = Query(None, description="Sort field, e.g. category, created_at"),
    desc: bool | None = Query(False),
    keywords: str | None = Query(None),
):
    """Paged market list with keyword search over name and description."""

    if page < 1 or pagesize < 1:
        raise BusinessException(PAGING_ERROR_MSG, BizCode.BAD_REQUEST)

    total, items = McpMarketService(db).get_markets_paginated(
        principal, page, pagesize, orderby, desc, keywords
    )

    page_items = [McpMarket.model_validate(item) for item in items]
    meta = PageMeta(
        page=page,
        pagesize=pagesize,
        total=total,
        hasnext=(page * pagesize) < total,
    )
    result = PageData(page=meta, items=page_items)
    return success(
        data=jsonable_encoder(result),
        msg="Query of mcp market list successful",
    )


@router.post("/mcp_market", summary="创建 MCP 市场", response_model=ApiResponse)
def create_mcp_market(create_data: McpMarketCreate, db: DbSession, principal: Caller):
    """Create a market owned by the calling principal."""

    market = McpMarketService(db).create_market(create_data, principal)
    return success(
        data=jsonable_encoder(McpMarket.model_validate(market)),
        msg="The mcp market has been successfully created",
    )


@router.get("/{mcp_market_id}", summary="获取 MCP 市场详情", response_model=ApiResponse)
def get_mcp_market(mcp_market_id: uuid.UUID, db: DbSession, principal: Caller):
    """Return one market or fail with the detail read's 404 copy."""

    market = McpMarketService(db).get_market(mcp_market_id)
    if not market:
        raise BusinessException(MARKET_NOT_FOUND_ACCESS_MSG, BizCode.NOT_FOUND)
    return success(
        data=jsonable_encoder(McpMarket.model_validate(market)),
        msg="Successfully obtained mcp market information",
    )


@router.put("/{mcp_market_id}", summary="更新 MCP 市场", response_model=ApiResponse)
def update_mcp_market(
    mcp_market_id: uuid.UUID,
    update_data: McpMarketUpdate,
    db: DbSession,
    principal: Caller,
):
    """Partially update a market (rename uniqueness enforced)."""

    service = McpMarketService(db)
    market = service.get_market(mcp_market_id)
    if not market:
        raise BusinessException(MARKET_NOT_FOUND_PERMISSION_MSG, BizCode.NOT_FOUND)

    market = service.update_market(market, update_data)
    return success(
        data=jsonable_encoder(McpMarket.model_validate(market)),
        msg="The mcp market information updated successfully",
    )


@router.delete("/{mcp_market_id}", summary="删除 MCP 市场", response_model=ApiResponse)
def delete_mcp_market(mcp_market_id: uuid.UUID, db: DbSession, principal: Caller):
    """Delete a market or fail with the write paths' 404 copy."""

    service = McpMarketService(db)
    market = service.get_market(mcp_market_id)
    if not market:
        raise BusinessException(MARKET_NOT_FOUND_PERMISSION_MSG, BizCode.NOT_FOUND)

    service.delete_market(mcp_market_id)
    return success(msg="The mcp market has been successfully deleted")
