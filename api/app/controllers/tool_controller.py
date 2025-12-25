"""工具控制器 - 简化统一的工具管理接口"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.schemas.tool_schema import (
    ToolCreateRequest, ToolUpdateRequest, ToolExecuteRequest, ParseSchemaRequest, CustomToolTestRequest
)

from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.models.tool_model import ToolType, ToolStatus, AuthType
from app.services.tool_service import ToolService
from app.schemas.response_schema import ApiResponse

router = APIRouter(prefix="/tools", tags=["Tool System"])


def get_tool_service(db: Session = Depends(get_db)) -> ToolService:
    return ToolService(db)


@router.get("/statistics", response_model=ApiResponse)
async def get_tool_statistics(
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """获取工具统计信息"""
    try:
        stats = service.get_tool_statistics(current_user.tenant_id)
        return success(data=stats, msg="获取统计信息成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=ApiResponse)
async def list_tools(
        name: Optional[str] = Query(None),
        tool_type: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """获取工具列表"""
    try:
        # 确保内置工具已初始化
        service.ensure_builtin_tools_initialized(current_user.tenant_id)

        # 获取工具列表
        tools = service.list_tools(
            tenant_id=current_user.tenant_id,
            name=name,
            tool_type=ToolType(tool_type) if tool_type else None,
            status=ToolStatus(status) if status else None
        )
        return success(data=tools, msg="获取工具列表成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{tool_id}", response_model=ApiResponse)
async def get_tool(
        tool_id: str,
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """获取工具详情"""
    tool = service.get_tool_info(tool_id, current_user.tenant_id)
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    return success(data=tool, msg="获取工具详情成功")


@router.post("", response_model=ApiResponse)
async def create_tool(
        request: ToolCreateRequest,
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """创建工具"""
    try:
        tool_id = service.create_tool(
            name=request.name,
            tool_type=request.tool_type,
            tenant_id=current_user.tenant_id,
            icon=request.icon,
            description=request.description,
            config=request.config
        )
        return success(data={"tool_id": tool_id}, msg="工具创建成功")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{tool_id}", response_model=ApiResponse)
async def update_tool(
        tool_id: str,
        request: ToolUpdateRequest,
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """更新工具"""
    try:
        success_flag = service.update_tool(
            tool_id=tool_id,
            tenant_id=current_user.tenant_id,
            name=request.name,
            description=request.description,
            icon=request.icon,
            config=request.config,
            is_enabled=request.config.get("is_enabled", None)
        )
        if not success_flag:
            raise HTTPException(status_code=404, detail="工具不存在")
        return success(msg="工具更新成功")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{tool_id}", response_model=ApiResponse)
async def delete_tool(
        tool_id: str,
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """删除工具"""
    try:
        success_flag = service.delete_tool(tool_id, current_user.tenant_id)
        if not success_flag:
            raise HTTPException(status_code=404, detail="工具不存在")
        return success(msg="工具删除成功")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execution/execute", response_model=ApiResponse)
async def execute_tool(
        request: ToolExecuteRequest,
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """执行工具"""
    try:
        result = await service.execute_tool(
            tool_id=request.tool_id,
            parameters=request.parameters,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            workspace_id=current_user.current_workspace_id,
            timeout=request.timeout
        )

        return success(
            data={
                "success": result.success,
                "data": result.data,
                "error": result.error,
                "execution_time": result.execution_time,
                "token_usage": result.token_usage
            },
            msg="工具执行完成"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse_schema", response_model=ApiResponse)
async def parse_openapi_schema(
    request: ParseSchemaRequest,
    current_user: User = Depends(get_current_user),
    service: ToolService = Depends(get_tool_service)
):
    """解析OpenAPI schema"""
    try:
        result = await service.parse_openapi_schema(request.schema_content, request.schema_url)
        if result["success"] is False:
            raise HTTPException(status_code=400, detail=result["message"])
        return success(data=result, msg="Schema解析完成")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{tool_id}/sync_mcp_tools", response_model=ApiResponse)
async def sync_mcp_tools(
    tool_id: str,
    current_user: User = Depends(get_current_user),
    service: ToolService = Depends(get_tool_service)
):
    """同步MCP工具列表"""
    try:
        result = await service.sync_mcp_tools(tool_id, current_user.tenant_id)
        if result["success"] is False:
            raise HTTPException(status_code=404, detail=result["message"])
        return success(data=result, msg="MCP工具列表同步完成")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{tool_id}/test", response_model=ApiResponse)
async def test_tool_connection(
        tool_id: str,
        test_request: Optional[CustomToolTestRequest] = None,
        current_user: User = Depends(get_current_user),
        service: ToolService = Depends(get_tool_service)
):
    """测试工具连接"""
    try:
        if test_request:
            # 自定义工具测试
            result = await service.test_custom_tool(
                tool_id, current_user.tenant_id, 
                test_request.method, test_request.path, test_request.parameters
            )
        else:
            # 普通连接测试
            result = await service.test_connection(tool_id, current_user.tenant_id)
        return success(data=result, msg="连接测试完成")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/enums/tool_types", response_model=ApiResponse)
async def get_tool_types():
    """获取工具类型枚举"""
    return success(
        data=[
            {"value": ToolType.BUILTIN.value, "label": "内置工具"},
            {"value": ToolType.CUSTOM.value, "label": "自定义工具"},
            {"value": ToolType.MCP.value, "label": "MCP工具"}
        ],
        msg="获取工具类型成功"
    )


@router.get("/enums/status", response_model=ApiResponse)
async def get_tool_status():
    """获取工具状态枚举"""
    return success(data=ToolStatus.get_all_statuses_with_labels(), msg="获取工具状态成功")


@router.get("/auth/types", response_model=ApiResponse)
async def get_auth_types():
    """获取认证类型枚举"""
    return success(data=AuthType.get_all_types_with_labels(), msg="获取认证类型成功")
