"""工具执行API控制器"""
import uuid
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.core.tools.registry import ToolRegistry
from app.core.tools.executor import ToolExecutor
from app.core.tools.chain_manager import ChainManager, ChainDefinition, ChainStep, ChainExecutionMode
from app.core.tools.builtin import *
from app.core.logging_config import get_business_logger

logger = get_business_logger()

router = APIRouter(prefix="/tools/execution", tags=["工具执行"])


# ==================== 请求/响应模型 ====================

class ToolExecutionRequest(BaseModel):
    """工具执行请求"""
    tool_id: str = Field(..., description="工具ID")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="工具参数")
    timeout: Optional[float] = Field(None, ge=1, le=300, description="超时时间（秒）")
    metadata: Optional[Dict[str, Any]] = Field(None, description="额外元数据")


class BatchExecutionRequest(BaseModel):
    """批量执行请求"""
    executions: List[ToolExecutionRequest] = Field(..., description="执行列表")
    max_concurrency: int = Field(5, ge=1, le=20, description="最大并发数")


class ToolExecutionResponse(BaseModel):
    """工具执行响应"""
    success: bool
    execution_id: str
    tool_id: str
    data: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    execution_time: float
    token_usage: Optional[Dict[str, int]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChainStepRequest(BaseModel):
    """链步骤请求"""
    tool_id: str = Field(..., description="工具ID")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="工具参数")
    condition: Optional[str] = Field(None, description="执行条件")
    output_mapping: Optional[Dict[str, str]] = Field(None, description="输出映射")
    error_handling: str = Field("stop", description="错误处理策略")


class ChainExecutionRequest(BaseModel):
    """链执行请求"""
    name: str = Field(..., description="链名称")
    description: str = Field("", description="链描述")
    steps: List[ChainStepRequest] = Field(..., description="执行步骤")
    execution_mode: str = Field("sequential", description="执行模式")
    initial_variables: Optional[Dict[str, Any]] = Field(None, description="初始变量")
    global_timeout: Optional[float] = Field(None, description="全局超时")


class ExecutionHistoryResponse(BaseModel):
    """执行历史响应"""
    execution_id: str
    tool_id: str
    status: str
    started_at: Optional[str]
    completed_at: Optional[str]
    execution_time: Optional[float]
    user_id: Optional[str]
    workspace_id: Optional[str]
    input_data: Optional[Dict[str, Any]]
    output_data: Optional[Any]
    error_message: Optional[str]
    token_usage: Optional[Dict[str, int]]


class ToolConnectionTestResponse(BaseModel):
    """工具连接测试响应"""
    success: bool
    message: str
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


# ==================== 依赖注入 ====================

def get_tool_registry(db: Session = Depends(get_db)) -> ToolRegistry:
    """获取工具注册表"""
    registry = ToolRegistry(db)
    
    # 注册内置工具类
    registry.register_tool_class(DateTimeTool)
    registry.register_tool_class(JsonTool)
    registry.register_tool_class(BaiduSearchTool)
    registry.register_tool_class(MinerUTool)
    registry.register_tool_class(TextInTool)
    
    return registry


def get_tool_executor(
    db: Session = Depends(get_db),
    registry: ToolRegistry = Depends(get_tool_registry)
) -> ToolExecutor:
    """获取工具执行器"""
    return ToolExecutor(db, registry)


def get_chain_manager(executor: ToolExecutor = Depends(get_tool_executor)) -> ChainManager:
    """获取链管理器"""
    return ChainManager(executor)


# ==================== API端点 ====================

@router.post("/execute", response_model=ToolExecutionResponse)
async def execute_tool(
    request: ToolExecutionRequest,
    current_user: User = Depends(get_current_user),
    executor: ToolExecutor = Depends(get_tool_executor)
):
    """执行单个工具"""
    try:
        # 生成执行ID
        execution_id = f"exec_{uuid.uuid4().hex[:16]}"
        
        # 执行工具
        result = await executor.execute_tool(
            tool_id=request.tool_id,
            parameters=request.parameters,
            user_id=current_user.id,
            workspace_id=current_user.current_workspace_id,
            execution_id=execution_id,
            timeout=request.timeout,
            metadata=request.metadata
        )
        
        return ToolExecutionResponse(
            success=result.success,
            execution_id=execution_id,
            tool_id=request.tool_id,
            data=result.data,
            error=result.error,
            error_code=result.error_code,
            execution_time=result.execution_time,
            token_usage=result.token_usage,
            metadata=result.metadata
        )
        
    except Exception as e:
        logger.error(f"工具执行失败: {request.tool_id}, 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch", response_model=List[ToolExecutionResponse])
async def execute_tools_batch(
    request: BatchExecutionRequest,
    current_user: User = Depends(get_current_user),
    executor: ToolExecutor = Depends(get_tool_executor)
):
    """批量执行工具"""
    try:
        # 准备执行配置
        execution_configs = []
        execution_ids = []
        
        for exec_request in request.executions:
            execution_id = f"exec_{uuid.uuid4().hex[:16]}"
            execution_ids.append(execution_id)
            
            execution_configs.append({
                "tool_id": exec_request.tool_id,
                "parameters": exec_request.parameters,
                "user_id": current_user.id,
                "workspace_id": current_user.current_workspace_id,
                "execution_id": execution_id,
                "timeout": exec_request.timeout,
                "metadata": exec_request.metadata
            })
        
        # 批量执行
        results = await executor.execute_tools_batch(
            execution_configs,
            max_concurrency=request.max_concurrency
        )
        
        # 转换响应格式
        responses = []
        for i, result in enumerate(results):
            responses.append(ToolExecutionResponse(
                success=result.success,
                execution_id=execution_ids[i],
                tool_id=request.executions[i].tool_id,
                data=result.data,
                error=result.error,
                error_code=result.error_code,
                execution_time=result.execution_time,
                token_usage=result.token_usage,
                metadata=result.metadata
            ))
        
        return responses
        
    except Exception as e:
        logger.error(f"批量执行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chain", response_model=Dict[str, Any])
async def execute_tool_chain(
    request: ChainExecutionRequest,
    current_user: User = Depends(get_current_user),
    chain_manager: ChainManager = Depends(get_chain_manager)
):
    """执行工具链"""
    try:
        # 转换步骤格式
        steps = []
        for step_request in request.steps:
            step = ChainStep(
                tool_id=step_request.tool_id,
                parameters=step_request.parameters,
                condition=step_request.condition,
                output_mapping=step_request.output_mapping,
                error_handling=step_request.error_handling
            )
            steps.append(step)
        
        # 创建链定义
        chain_definition = ChainDefinition(
            name=request.name,
            description=request.description,
            steps=steps,
            execution_mode=ChainExecutionMode(request.execution_mode),
            global_timeout=request.global_timeout
        )
        
        # 注册并执行链
        chain_manager.register_chain(chain_definition)
        
        result = await chain_manager.execute_chain(
            chain_name=request.name,
            initial_variables=request.initial_variables
        )
        
        return result
        
    except Exception as e:
        logger.error(f"工具链执行失败: {request.name}, 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/running", response_model=List[Dict[str, Any]])
async def get_running_executions(
    current_user: User = Depends(get_current_user),
    executor: ToolExecutor = Depends(get_tool_executor)
):
    """获取正在运行的执行"""
    try:
        running_executions = executor.get_running_executions()
        
        # 过滤当前工作空间的执行
        workspace_executions = [
            exec_info for exec_info in running_executions
            if exec_info.get("workspace_id") == str(current_user.current_workspace_id)
        ]
        
        return workspace_executions
        
    except Exception as e:
        logger.error(f"获取运行中执行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cancel/{execution_id}", response_model=Dict[str, Any])
async def cancel_execution(
    execution_id: str = Path(..., description="执行ID"),
    current_user: User = Depends(get_current_user),
    executor: ToolExecutor = Depends(get_tool_executor)
):
    """取消工具执行"""
    try:
        success = await executor.cancel_execution(execution_id)
        
        if success:
            return {
                "success": True,
                "message": "执行已取消"
            }
        else:
            raise HTTPException(status_code=404, detail="执行不存在或已完成")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消执行失败: {execution_id}, 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=List[ExecutionHistoryResponse])
async def get_execution_history(
    tool_id: Optional[str] = Query(None, description="工具ID过滤"),
    limit: int = Query(50, ge=1, le=200, description="返回数量限制"),
    current_user: User = Depends(get_current_user),
    executor: ToolExecutor = Depends(get_tool_executor)
):
    """获取执行历史"""
    try:
        history = executor.get_execution_history(
            tool_id=tool_id,
            user_id=current_user.id,
            workspace_id=current_user.current_workspace_id,
            limit=limit
        )
        
        # 转换响应格式
        responses = []
        for record in history:
            responses.append(ExecutionHistoryResponse(
                execution_id=record["execution_id"],
                tool_id=record["tool_id"],
                status=record["status"],
                started_at=record["started_at"],
                completed_at=record["completed_at"],
                execution_time=record["execution_time"],
                user_id=record["user_id"],
                workspace_id=record["workspace_id"],
                input_data=record["input_data"],
                output_data=record["output_data"],
                error_message=record["error_message"],
                token_usage=record["token_usage"]
            ))
        
        return responses
        
    except Exception as e:
        logger.error(f"获取执行历史失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/statistics", response_model=Dict[str, Any])
async def get_execution_statistics(
    days: int = Query(7, ge=1, le=90, description="统计天数"),
    current_user: User = Depends(get_current_user),
    executor: ToolExecutor = Depends(get_tool_executor)
):
    """获取执行统计"""
    try:
        stats = executor.get_execution_statistics(
            workspace_id=current_user.current_workspace_id,
            days=days
        )
        
        return {
            "success": True,
            "statistics": stats
        }
        
    except Exception as e:
        logger.error(f"获取执行统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chains/running", response_model=List[Dict[str, Any]])
async def get_running_chains(
    current_user: User = Depends(get_current_user),
    chain_manager: ChainManager = Depends(get_chain_manager)
):
    """获取正在运行的工具链"""
    try:
        running_chains = chain_manager.get_running_chains()
        return running_chains
        
    except Exception as e:
        logger.error(f"获取运行中工具链失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chains", response_model=List[Dict[str, Any]])
async def list_tool_chains(
    current_user: User = Depends(get_current_user),
    chain_manager: ChainManager = Depends(get_chain_manager)
):
    """列出工具链"""
    try:
        chains = chain_manager.list_chains()
        return chains
        
    except Exception as e:
        logger.error(f"获取工具链列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test-connection/{tool_id}", response_model=ToolConnectionTestResponse)
async def test_tool_connection(
    tool_id: str = Path(..., description="工具ID"),
    current_user: User = Depends(get_current_user),
    executor: ToolExecutor = Depends(get_tool_executor)
):
    """测试工具连接"""
    try:
        result = await executor.test_tool_connection(
            tool_id=tool_id,
            user_id=current_user.id,
            workspace_id=current_user.current_workspace_id
        )
        
        return ToolConnectionTestResponse(
            success=result.get("success", False),
            message=result.get("message", ""),
            error=result.get("error"),
            details=result.get("details")
        )
        
    except Exception as e:
        logger.error(f"工具连接测试失败: {tool_id}, 错误: {e}")
        return ToolConnectionTestResponse(
            success=False,
            message="连接测试失败",
            error=str(e)
        )