"""MCP market config routes (monolith parity).

Wire-compatible port of the host ``mcp_market_config_controller``: the
ModelScope listing probes and the config CRUD endpoints under the host's
``/mcp_market_configs`` prefix. The literal listing paths are declared before
the ``/{mcp_market_config_id}`` paths on purpose.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...schemas.mcp_market_config_schema import (
    McpMarketConfig,
    McpMarketConfigCreate,
    McpMarketConfigUpdate,
)
from ...services.mcp_market_config_service import McpMarketConfigService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/mcp_market_configs", tags=["mcp_market_configs"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]

# Host copy kept verbatim; the missing-config branch is a 200 envelope, while
# the two token failures use different texts (stored token vs create payload).
CONFIG_MISSING_MSG = "The mcp market config does not exist or access is denied"
CONFIG_TOKEN_MISSING_MSG = "MCP market config token is not configured"
PAGING_ERROR_MSG = "The paging parameter must be greater than 0"
MODELSCOPE_LIMIT_MSG = (
    "The maximum number of MCP services can view is 100. "
    "Please visit the ModelScope MCP Plaza."
)


@router.get(
    "/mcp_servers",
    summary="分页查询 ModelScope MCP 服务列表",
    response_model=ApiResponse,
)
def get_mcp_servers(
    db: DbSession,
    principal: Caller,
    mcp_market_config_id: uuid.UUID,
    page: int = Query(1, gt=0),
    pagesize: int = Query(20, gt=0, le=100),
    keywords: str | None = Query(None),
):
    """Paged ModelScope listing; writes the total back to the market."""

    if page < 1 or pagesize < 1:
        raise BusinessException(PAGING_ERROR_MSG, BizCode.BAD_REQUEST)
    if page * pagesize > 100:
        raise BusinessException(MODELSCOPE_LIMIT_MSG, BizCode.INVALID_PARAMETER)

    service = McpMarketConfigService(db)
    config = service.get_config(mcp_market_config_id)
    if not config:
        return success(msg=CONFIG_MISSING_MSG)
    if not config.token:
        raise BusinessException(CONFIG_TOKEN_MISSING_MSG, BizCode.INVALID_PARAMETER)

    total, mcp_server_list = service.list_market_servers(
        config.token, page, pagesize, keywords
    )
    meta = PageMeta(
        page=page,
        pagesize=pagesize,
        total=total,
        hasnext=(page * pagesize) < total,
    )
    result = PageData(page=meta, items=mcp_server_list)
    service.sync_market_count(config.mcp_market_id, total)
    return success(
        data=jsonable_encoder(result),
        msg="Query of mcp servers list successful",
    )


@router.get(
    "/operational_mcp_servers",
    summary="查询运营中的 ModelScope MCP 服务列表",
    response_model=ApiResponse,
)
def get_operational_mcp_servers(
    db: DbSession,
    principal: Caller,
    mcp_market_config_id: uuid.UUID,
):
    """Operational ModelScope listing as a bare list."""

    service = McpMarketConfigService(db)
    config = service.get_config(mcp_market_config_id)
    if not config:
        return success(msg=CONFIG_MISSING_MSG)
    if not config.token:
        raise BusinessException(CONFIG_TOKEN_MISSING_MSG, BizCode.INVALID_PARAMETER)

    servers = service.list_operational_servers(config.token)
    return success(
        data=servers,
        msg="Query of operational mcp servers list successful",
    )


@router.get(
    "/mcp_server",
    summary="获取 ModelScope MCP 服务详情",
    response_model=ApiResponse,
)
def get_mcp_server(
    db: DbSession,
    principal: Caller,
    mcp_market_config_id: uuid.UUID,
    server_id: str,
):
    """One ModelScope MCP server detail (host success copy)."""

    service = McpMarketConfigService(db)
    config = service.get_config(mcp_market_config_id)
    if not config:
        return success(msg=CONFIG_MISSING_MSG)
    if not config.token:
        raise BusinessException(CONFIG_TOKEN_MISSING_MSG, BizCode.INVALID_PARAMETER)

    result = service.get_server(config.token, server_id)
    return success(data=result, msg="Query of mcp servers list successful")


@router.post(
    "/mcp_market_config",
    summary="创建 MCP 市场配置",
    response_model=ApiResponse,
)
def create_mcp_market_config(
    create_data: McpMarketConfigCreate,
    db: DbSession,
    principal: Caller,
):
    """Validate the token, probe connectivity and persist the config."""

    config = McpMarketConfigService(db).create_config(create_data, principal)
    return success(
        data=jsonable_encoder(McpMarketConfig.model_validate(config)),
        msg="The mcp market config has been successfully created",
    )


@router.get(
    "/{mcp_market_config_id}",
    summary="获取 MCP 市场配置详情",
    response_model=ApiResponse,
)
def get_mcp_market_config(
    mcp_market_config_id: uuid.UUID,
    db: DbSession,
    principal: Caller,
):
    """Config detail by config id (host parity: no tenant filter)."""

    config = McpMarketConfigService(db).get_config(mcp_market_config_id)
    if not config:
        return success(msg=CONFIG_MISSING_MSG)
    return success(
        data=jsonable_encoder(McpMarketConfig.model_validate(config)),
        msg="Successfully obtained mcp market config information",
    )


@router.get(
    "/mcp_market_id/{mcp_market_id}",
    summary="按 MCP 市场 ID 获取配置详情",
    response_model=ApiResponse,
)
def get_mcp_market_config_by_mcp_market_id(
    mcp_market_id: uuid.UUID,
    db: DbSession,
    principal: Caller,
):
    """Config detail by market id, scoped to the caller's tenant."""

    config = McpMarketConfigService(db).get_config_by_market_id(
        mcp_market_id, principal.tenant_id
    )
    if not config:
        return success(msg=CONFIG_MISSING_MSG)
    return success(
        data=jsonable_encoder(McpMarketConfig.model_validate(config)),
        msg="Successfully obtained mcp market config information",
    )


@router.put(
    "/{mcp_market_config_id}",
    summary="更新 MCP 市场配置",
    response_model=ApiResponse,
)
def update_mcp_market_config(
    mcp_market_config_id: uuid.UUID,
    update_data: McpMarketConfigUpdate,
    db: DbSession,
    principal: Caller,
):
    """Partially update a config; a supplied token is re-validated."""

    service = McpMarketConfigService(db)
    config = service.get_config(mcp_market_config_id)
    if not config:
        return success(msg=CONFIG_MISSING_MSG)

    config = service.update_config(config, update_data)
    return success(
        data=jsonable_encoder(McpMarketConfig.model_validate(config)),
        msg="The mcp market config information updated successfully",
    )


@router.delete(
    "/{mcp_market_config_id}",
    summary="删除 MCP 市场配置",
    response_model=ApiResponse,
)
def delete_mcp_market_config(
    mcp_market_config_id: uuid.UUID,
    db: DbSession,
    principal: Caller,
):
    """Delete a config by id."""

    service = McpMarketConfigService(db)
    config = service.get_config(mcp_market_config_id)
    if not config:
        return success(msg=CONFIG_MISSING_MSG)

    service.delete_config(mcp_market_config_id)
    return success(msg="The mcp market config has been successfully deleted")
