"""Tool family management routes.

Mirrors the monolith ``tool_controller`` definition-plane endpoints (list,
detail, CRUD, active toggle, method discovery, statistics, schema parsing and
enums). Execution-plane endpoints (execute, connection test, MCP tool sync)
stay in the monolith for now. The async service methods that bridge to MCP
discovery run through ``asyncio.run`` on the sync threadpool handler.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...errors import BusinessException
from ...models.tool_model import AuthType, ToolStatus, ToolType
from ...schemas.tool_schema import (
    ParseSchemaRequest,
    ToolActiveUpdate,
    ToolCreateRequest,
    ToolUpdateRequest,
)
from ...services.tool_service import ToolService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/tools", tags=["Tool System"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]


@router.get("/statistics", summary="获取工具统计信息", response_model=ApiResponse)
def get_tool_statistics(db: DbSession, principal: Caller):
    """Get tool statistics"""
    try:
        stats = ToolService(db).get_tool_statistics(principal.tenant_id)
        return success(data=stats, msg="获取统计信息成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("", summary="获取工具列表", response_model=ApiResponse)
def list_tools(
        db: DbSession,
        principal: Caller,
        name: str | None = Query(None),
        tool_type: str | None = Query(None),
        status: str | None = Query(None),
):
    """List tools"""
    try:
        service = ToolService(db)
        # Make sure the builtin tools are initialized
        service.ensure_builtin_tools_initialized(principal.tenant_id)

        # Fetch the tool list
        tools = service.list_tools(
            tenant_id=principal.tenant_id,
            name=name,
            tool_type=ToolType(tool_type) if tool_type else None,
            status=ToolStatus(status) if status else None
        )
        return success(data=tools, msg="获取工具列表成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{tool_id}/methods", summary="获取工具的所有方法", response_model=ApiResponse)
def get_tool_methods(tool_id: str, db: DbSession, principal: Caller):
    """Get all methods of a tool"""
    try:
        methods = asyncio.run(ToolService(db).get_tool_methods(tool_id, principal.tenant_id))
        if methods is None:
            raise HTTPException(status_code=404, detail="工具不存在")
        return success(data=methods, msg="获取工具方法成功")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{tool_id}", summary="获取工具详情", response_model=ApiResponse)
def get_tool(tool_id: str, db: DbSession, principal: Caller):
    """Get tool details"""
    tool = ToolService(db).get_tool_detail(tool_id, principal.tenant_id)
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    return success(data=tool, msg="获取工具详情成功")


@router.post("", summary="创建工具", response_model=ApiResponse)
def create_tool(request: ToolCreateRequest, db: DbSession, principal: Caller):
    """Create a tool"""
    try:
        # Merge the MCP source fields into config
        if request.tool_type == ToolType.MCP:
            for key in ("source_channel", "market_id", "market_config_id", "mcp_service_id"):
                val = getattr(request, key, None)
                if val is not None:
                    request.config[key] = val
        tool_id = asyncio.run(ToolService(db).create_tool(
            name=request.name,
            tool_type=request.tool_type,
            tenant_id=principal.tenant_id,
            icon=request.icon,
            description=request.description,
            config=request.config,
            tags=request.tags
        ))
        return success(data={"tool_id": tool_id}, msg="工具创建成功")
    except BusinessException as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/{tool_id}", summary="更新工具", response_model=ApiResponse)
def update_tool(tool_id: str, request: ToolUpdateRequest, db: DbSession, principal: Caller):
    """Update a tool"""
    try:
        success_flag = ToolService(db).update_tool(
            tool_id=tool_id,
            tenant_id=principal.tenant_id,
            name=request.name,
            description=request.description,
            icon=request.icon,
            config=request.config,
            is_enabled=request.config.get("is_enabled", None),
            tags=request.tags
        )
        if not success_flag:
            raise HTTPException(status_code=404, detail="工具不存在")
        return success(msg="工具更新成功")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{tool_id}", summary="删除工具", response_model=ApiResponse)
def delete_tool(tool_id: str, db: DbSession, principal: Caller):
    """Delete a tool (soft delete, is_active=False)"""
    try:
        success_flag = ToolService(db).delete_tool(tool_id, principal.tenant_id)
        if not success_flag:
            raise HTTPException(status_code=404, detail="工具不存在")
        return success(msg="工具删除成功")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.patch("/{tool_id}/active", summary="设置工具可用状态", response_model=ApiResponse)
def set_tool_active(tool_id: str, request: ToolActiveUpdate, db: DbSession, principal: Caller):
    """Set the tool availability state (enable / disable)

    - is_active=true: enable the tool
    - is_active=false: disable the tool (equivalent to deleting, but recoverable)
    """
    try:
        success_flag = ToolService(db).set_tool_active(
            tool_id, principal.tenant_id, request.is_active
        )
        if not success_flag:
            raise HTTPException(status_code=404, detail="工具不存在")
        action = "启用" if request.is_active else "禁用"
        return success(msg=f"工具已{action}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/parse_schema", summary="解析OpenAPI schema", response_model=ApiResponse)
def parse_openapi_schema(request: ParseSchemaRequest, db: DbSession, principal: Caller):
    """Parse an OpenAPI schema"""
    try:
        result = ToolService.parse_openapi_schema(request.schema_content, request.schema_url)
        if result["success"] is False:
            raise HTTPException(status_code=400, detail=result["message"])
        return success(data=result, msg="Schema解析完成")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/enums/tool_types", summary="获取工具类型枚举", response_model=ApiResponse)
def get_tool_types():
    """Get the tool-type enum"""
    return success(
        data=[
            {"value": ToolType.BUILTIN.value, "label": "内置工具"},
            {"value": ToolType.CUSTOM.value, "label": "自定义工具"},
            {"value": ToolType.MCP.value, "label": "MCP工具"},
            {"value": ToolType.WORKFLOW.value, "label": "工作流工具"},
        ],
        msg="获取工具类型成功"
    )


@router.get("/enums/status", summary="获取工具状态枚举", response_model=ApiResponse)
def get_tool_status():
    """Get the tool-status enum"""
    return success(data=ToolStatus.get_all_statuses_with_labels(), msg="获取工具状态成功")


@router.get("/auth/types", summary="获取认证类型枚举", response_model=ApiResponse)
def get_auth_types():
    """Get the auth-type enum"""
    return success(data=AuthType.get_all_types_with_labels(), msg="获取认证类型成功")
