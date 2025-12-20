"""工具执行器 - 负责工具的实际调用和执行管理"""
import asyncio
import uuid
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.tool_model import ToolExecution, ExecutionStatus
from app.core.tools.base import BaseTool, ToolResult
from app.core.tools.registry import ToolRegistry
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class ExecutionContext:
    """执行上下文"""
    
    def __init__(
        self,
        execution_id: str,
        tool_id: str,
        user_id: Optional[uuid.UUID] = None,
        workspace_id: Optional[uuid.UUID] = None,
        timeout: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.execution_id = execution_id
        self.tool_id = tool_id
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.timeout = timeout or 60.0  # 默认60秒超时
        self.metadata = metadata or {}
        self.started_at = datetime.now()
        self.completed_at: Optional[datetime] = None
        self.status = ExecutionStatus.PENDING


class ToolExecutor:
    """工具执行器 - 使用langchain标准接口执行工具"""
    
    def __init__(self, db: Session, registry: ToolRegistry):
        """初始化工具执行器
        
        Args:
            db: 数据库会话
            registry: 工具注册表
        """
        self.db = db
        self.registry = registry
        self._running_executions: Dict[str, ExecutionContext] = {}
        self._execution_lock = asyncio.Lock()
    
    async def execute_tool(
        self,
        tool_id: str,
        parameters: Dict[str, Any],
        user_id: Optional[uuid.UUID] = None,
        workspace_id: Optional[uuid.UUID] = None,
        execution_id: Optional[str] = None,
        timeout: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ToolResult:
        """执行工具
        
        Args:
            tool_id: 工具ID
            parameters: 工具参数
            user_id: 用户ID
            workspace_id: 工作空间ID
            execution_id: 执行ID（可选，自动生成）
            timeout: 超时时间（秒）
            metadata: 额外元数据
            
        Returns:
            工具执行结果
        """
        # 生成执行ID
        if not execution_id:
            execution_id = f"exec_{uuid.uuid4().hex[:16]}"
        
        # 创建执行上下文
        context = ExecutionContext(
            execution_id=execution_id,
            tool_id=tool_id,
            user_id=user_id,
            workspace_id=workspace_id,
            timeout=timeout,
            metadata=metadata
        )
        
        try:
            # 获取工具实例
            tool = self.registry.get_tool(tool_id)
            if not tool:
                return ToolResult.error_result(
                    error=f"工具不存在: {tool_id}",
                    error_code="TOOL_NOT_FOUND",
                    execution_time=0.0
                )
            
            # 记录执行开始
            await self._record_execution_start(context, parameters)
            
            # 执行工具
            result = await self._execute_with_timeout(tool, parameters, context)
            
            # 记录执行完成
            await self._record_execution_complete(context, result)
            
            return result
            
        except Exception as e:
            logger.error(f"工具执行异常: {execution_id}, 错误: {e}")
            
            # 记录执行失败
            error_result = ToolResult.error_result(
                error=str(e),
                error_code="EXECUTION_ERROR",
                execution_time=time.time() - context.started_at.timestamp()
            )
            await self._record_execution_complete(context, error_result)
            
            return error_result
        
        finally:
            # 清理执行上下文
            async with self._execution_lock:
                if execution_id in self._running_executions:
                    del self._running_executions[execution_id]
    
    async def execute_tools_batch(
        self,
        tool_executions: List[Dict[str, Any]],
        max_concurrency: int = 5
    ) -> List[ToolResult]:
        """批量执行工具
        
        Args:
            tool_executions: 工具执行配置列表，每个包含tool_id和parameters
            max_concurrency: 最大并发数
            
        Returns:
            执行结果列表
        """
        semaphore = asyncio.Semaphore(max_concurrency)
        
        async def execute_single(exec_config: Dict[str, Any]) -> ToolResult:
            async with semaphore:
                return await self.execute_tool(
                    tool_id=exec_config["tool_id"],
                    parameters=exec_config.get("parameters", {}),
                    user_id=exec_config.get("user_id"),
                    workspace_id=exec_config.get("workspace_id"),
                    timeout=exec_config.get("timeout"),
                    metadata=exec_config.get("metadata")
                )
        
        # 并发执行所有工具
        tasks = [execute_single(config) for config in tool_executions]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常结果
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(
                    ToolResult.error_result(
                        error=str(result),
                        error_code="BATCH_EXECUTION_ERROR",
                        execution_time=0.0
                    )
                )
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def cancel_execution(self, execution_id: str) -> bool:
        """取消工具执行
        
        Args:
            execution_id: 执行ID
            
        Returns:
            是否成功取消
        """
        async with self._execution_lock:
            if execution_id not in self._running_executions:
                return False
            
            context = self._running_executions[execution_id]
            context.status = ExecutionStatus.FAILED
            
            # 更新数据库记录
            execution_record = self.db.query(ToolExecution).filter(
                ToolExecution.execution_id == execution_id
            ).first()
            
            if execution_record:
                execution_record.status = ExecutionStatus.FAILED.value
                execution_record.error_message = "执行被取消"
                execution_record.completed_at = datetime.now()
                self.db.commit()
            
            logger.info(f"工具执行已取消: {execution_id}")
            return True
    
    def get_running_executions(self) -> List[Dict[str, Any]]:
        """获取正在运行的执行列表
        
        Returns:
            执行信息列表
        """
        executions = []
        for execution_id, context in self._running_executions.items():
            executions.append({
                "execution_id": execution_id,
                "tool_id": context.tool_id,
                "user_id": str(context.user_id) if context.user_id else None,
                "workspace_id": str(context.workspace_id) if context.workspace_id else None,
                "started_at": context.started_at.isoformat(),
                "status": context.status.value,
                "elapsed_time": (datetime.now() - context.started_at).total_seconds()
            })
        
        return executions
    
    async def _execute_with_timeout(
        self,
        tool: BaseTool,
        parameters: Dict[str, Any],
        context: ExecutionContext
    ) -> ToolResult:
        """带超时的工具执行
        
        Args:
            tool: 工具实例
            parameters: 参数
            context: 执行上下文
            
        Returns:
            执行结果
        """
        async with self._execution_lock:
            self._running_executions[context.execution_id] = context
            context.status = ExecutionStatus.RUNNING
        
        try:
            # 使用asyncio.wait_for实现超时控制
            result = await asyncio.wait_for(
                tool.safe_execute(**parameters),
                timeout=context.timeout
            )
            
            context.status = ExecutionStatus.COMPLETED
            return result
            
        except asyncio.TimeoutError:
            context.status = ExecutionStatus.TIMEOUT
            return ToolResult.error_result(
                error=f"工具执行超时（{context.timeout}秒）",
                error_code="EXECUTION_TIMEOUT",
                execution_time=context.timeout
            )
        
        except Exception as e:
            context.status = ExecutionStatus.FAILED
            raise
    
    async def _record_execution_start(
        self,
        context: ExecutionContext,
        parameters: Dict[str, Any]
    ):
        """记录执行开始"""
        try:
            execution_record = ToolExecution(
                execution_id=context.execution_id,
                tool_config_id=uuid.UUID(context.tool_id),
                status=ExecutionStatus.RUNNING.value,
                input_data=parameters,
                started_at=context.started_at,
                user_id=context.user_id,
                workspace_id=context.workspace_id
            )
            
            self.db.add(execution_record)
            self.db.commit()
            
            logger.debug(f"执行记录已创建: {context.execution_id}")
            
        except Exception as e:
            logger.error(f"创建执行记录失败: {context.execution_id}, 错误: {e}")
    
    async def _record_execution_complete(
        self,
        context: ExecutionContext,
        result: ToolResult
    ):
        """记录执行完成"""
        try:
            context.completed_at = datetime.now()
            
            execution_record = self.db.query(ToolExecution).filter(
                ToolExecution.execution_id == context.execution_id
            ).first()
            
            if execution_record:
                execution_record.status = (
                    ExecutionStatus.COMPLETED.value if result.success 
                    else ExecutionStatus.FAILED.value
                )
                execution_record.output_data = result.data if result.success else None
                execution_record.error_message = result.error if not result.success else None
                execution_record.completed_at = context.completed_at
                execution_record.execution_time = result.execution_time
                execution_record.token_usage = result.token_usage
                
                self.db.commit()
                
                logger.debug(f"执行记录已更新: {context.execution_id}")
            
        except Exception as e:
            logger.error(f"更新执行记录失败: {context.execution_id}, 错误: {e}")
    
    def get_execution_history(
        self,
        tool_id: Optional[str] = None,
        user_id: Optional[uuid.UUID] = None,
        workspace_id: Optional[uuid.UUID] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取执行历史
        
        Args:
            tool_id: 工具ID过滤
            user_id: 用户ID过滤
            workspace_id: 工作空间ID过滤
            limit: 返回数量限制
            
        Returns:
            执行历史列表
        """
        try:
            query = self.db.query(ToolExecution).order_by(
                ToolExecution.started_at.desc()
            )
            
            if tool_id:
                query = query.filter(ToolExecution.tool_config_id == uuid.UUID(tool_id))
            
            if user_id:
                query = query.filter(ToolExecution.user_id == user_id)
            
            if workspace_id:
                query = query.filter(ToolExecution.workspace_id == workspace_id)
            
            executions = query.limit(limit).all()
            
            history = []
            for execution in executions:
                history.append({
                    "execution_id": execution.execution_id,
                    "tool_id": str(execution.tool_config_id),
                    "status": execution.status,
                    "started_at": execution.started_at.isoformat() if execution.started_at else None,
                    "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
                    "execution_time": execution.execution_time,
                    "user_id": str(execution.user_id) if execution.user_id else None,
                    "workspace_id": str(execution.workspace_id) if execution.workspace_id else None,
                    "input_data": execution.input_data,
                    "output_data": execution.output_data,
                    "error_message": execution.error_message,
                    "token_usage": execution.token_usage
                })
            
            return history
            
        except Exception as e:
            logger.error(f"获取执行历史失败, 错误: {e}")
            return []
    
    def get_execution_statistics(
        self,
        workspace_id: Optional[uuid.UUID] = None,
        days: int = 7
    ) -> Dict[str, Any]:
        """获取执行统计信息
        
        Args:
            workspace_id: 工作空间ID
            days: 统计天数
            
        Returns:
            统计信息
        """
        try:
            from datetime import timedelta
            
            start_date = datetime.now() - timedelta(days=days)
            
            query = self.db.query(ToolExecution).filter(
                ToolExecution.started_at >= start_date
            )
            
            if workspace_id:
                query = query.filter(ToolExecution.workspace_id == workspace_id)
            
            executions = query.all()
            
            # 统计数据
            total_executions = len(executions)
            successful_executions = len([e for e in executions if e.status == ExecutionStatus.COMPLETED.value])
            failed_executions = len([e for e in executions if e.status == ExecutionStatus.FAILED.value])
            
            # 平均执行时间
            completed_executions = [e for e in executions if e.execution_time is not None]
            avg_execution_time = (
                sum(e.execution_time for e in completed_executions) / len(completed_executions)
                if completed_executions else 0
            )
            
            # 按工具统计
            tool_stats = {}
            for execution in executions:
                tool_id = str(execution.tool_config_id)
                if tool_id not in tool_stats:
                    tool_stats[tool_id] = {"total": 0, "successful": 0, "failed": 0}
                
                tool_stats[tool_id]["total"] += 1
                if execution.status == ExecutionStatus.COMPLETED.value:
                    tool_stats[tool_id]["successful"] += 1
                elif execution.status == ExecutionStatus.FAILED.value:
                    tool_stats[tool_id]["failed"] += 1
            
            return {
                "period_days": days,
                "total_executions": total_executions,
                "successful_executions": successful_executions,
                "failed_executions": failed_executions,
                "success_rate": successful_executions / total_executions if total_executions > 0 else 0,
                "average_execution_time": avg_execution_time,
                "tool_statistics": tool_stats
            }
            
        except Exception as e:
            logger.error(f"获取执行统计失败, 错误: {e}")
            return {}
    
    async def test_tool_connection(
        self,
        tool_id: str,
        user_id: Optional[uuid.UUID] = None,
        workspace_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """测试工具连接"""
        try:
            from app.models.tool_model import ToolConfig, ToolType, MCPToolConfig
            from .mcp.client import MCPClient
            
            tool_config = self.db.query(ToolConfig).filter(
                ToolConfig.id == uuid.UUID(tool_id)
            ).first()
            
            if not tool_config:
                return {"success": False, "message": "工具不存在"}
            
            if tool_config.tool_type == ToolType.MCP.value:
                mcp_config = self.db.query(MCPToolConfig).filter(
                    MCPToolConfig.id == tool_config.id
                ).first()
                
                if not mcp_config:
                    return {"success": False, "message": "MCP配置不存在"}
                
                client = MCPClient(mcp_config.server_url, mcp_config.connection_config or {})
                
                if await client.connect():
                    try:
                        tools = await client.list_tools()
                        await client.disconnect()
                        return {
                            "success": True,
                            "message": "MCP连接成功",
                            "details": {"server_url": mcp_config.server_url, "tools": len(tools)}
                        }
                    except:
                        await client.disconnect()
                        return {"success": False, "message": "MCP功能测试失败"}
                else:
                    return {"success": False, "message": "MCP连接失败"}
            else:
                tool = self.registry.get_tool(tool_id)
                if tool and hasattr(tool, 'test_connection'):
                    result = tool.test_connection()
                    return {"success": result.get("success", False), "message": result.get("message", "")}
                return {"success": True, "message": "工具无需连接测试"}
        except Exception as e:
            return {"success": False, "message": "测试失败", "error": str(e)}