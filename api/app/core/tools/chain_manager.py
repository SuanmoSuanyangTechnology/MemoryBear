"""工具链管理器 - 支持langchain的工具链模式"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from app.core.tools.base import ToolResult
from app.core.tools.executor import ToolExecutor
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class ChainExecutionMode(str, Enum):
    """链执行模式"""
    SEQUENTIAL = "sequential"  # 顺序执行
    PARALLEL = "parallel"     # 并行执行
    CONDITIONAL = "conditional"  # 条件执行


@dataclass
class ChainStep:
    """链步骤定义"""
    tool_id: str
    parameters: Dict[str, Any]
    condition: Optional[str] = None  # 执行条件
    output_mapping: Optional[Dict[str, str]] = None  # 输出映射
    error_handling: str = "stop"  # 错误处理：stop, continue, retry


@dataclass
class ChainDefinition:
    """工具链定义"""
    name: str
    description: str
    steps: List[ChainStep]
    execution_mode: ChainExecutionMode = ChainExecutionMode.SEQUENTIAL
    global_timeout: Optional[float] = None
    retry_policy: Optional[Dict[str, Any]] = None


class ChainExecutionContext:
    """链执行上下文"""
    
    def __init__(self, chain_id: str):
        self.chain_id = chain_id
        self.variables: Dict[str, Any] = {}
        self.step_results: Dict[int, ToolResult] = {}
        self.current_step = 0
        self.is_completed = False
        self.is_failed = False
        self.error_message: Optional[str] = None


class ChainManager:
    """工具链管理器 - 支持langchain的工具链模式"""
    
    def __init__(self, executor: ToolExecutor):
        """初始化工具链管理器
        
        Args:
            executor: 工具执行器
        """
        self.executor = executor
        self._chains: Dict[str, ChainDefinition] = {}
        self._running_chains: Dict[str, ChainExecutionContext] = {}
    
    def register_chain(self, chain: ChainDefinition) -> bool:
        """注册工具链
        
        Args:
            chain: 工具链定义
            
        Returns:
            注册是否成功
        """
        try:
            # 验证工具链定义
            validation_result = self._validate_chain(chain)
            if not validation_result[0]:
                logger.error(f"工具链验证失败: {chain.name}, 错误: {validation_result[1]}")
                return False
            
            self._chains[chain.name] = chain
            logger.info(f"工具链注册成功: {chain.name}")
            return True
            
        except Exception as e:
            logger.error(f"工具链注册失败: {chain.name}, 错误: {e}")
            return False
    
    def unregister_chain(self, chain_name: str) -> bool:
        """注销工具链
        
        Args:
            chain_name: 工具链名称
            
        Returns:
            注销是否成功
        """
        if chain_name in self._chains:
            del self._chains[chain_name]
            logger.info(f"工具链注销成功: {chain_name}")
            return True
        
        return False
    
    def list_chains(self) -> List[Dict[str, Any]]:
        """列出所有工具链
        
        Returns:
            工具链信息列表
        """
        chains = []
        for name, chain in self._chains.items():
            chains.append({
                "name": name,
                "description": chain.description,
                "step_count": len(chain.steps),
                "execution_mode": chain.execution_mode.value,
                "global_timeout": chain.global_timeout
            })
        
        return chains
    
    async def execute_chain(
        self,
        chain_name: str,
        initial_variables: Optional[Dict[str, Any]] = None,
        chain_id: Optional[str] = None
    ) -> Dict[str, Any] | None:
        """执行工具链
        
        Args:
            chain_name: 工具链名称
            initial_variables: 初始变量
            chain_id: 链执行ID（可选）
            
        Returns:
            执行结果
        """
        if chain_name not in self._chains:
            return {
                "success": False,
                "error": f"工具链不存在: {chain_name}",
                "chain_id": chain_id
            }
        
        chain = self._chains[chain_name]
        
        # 生成链ID
        if not chain_id:
            import uuid
            chain_id = f"chain_{uuid.uuid4().hex[:16]}"
        
        # 创建执行上下文
        context = ChainExecutionContext(chain_id)
        context.variables = initial_variables or {}
        self._running_chains[chain_id] = context
        
        try:
            logger.info(f"开始执行工具链: {chain_name} (ID: {chain_id})")
            
            # 根据执行模式执行
            if chain.execution_mode == ChainExecutionMode.SEQUENTIAL:
                result = await self._execute_sequential(chain, context)
            elif chain.execution_mode == ChainExecutionMode.PARALLEL:
                result = await self._execute_parallel(chain, context)
            elif chain.execution_mode == ChainExecutionMode.CONDITIONAL:
                result = await self._execute_conditional(chain, context)
            else:
                raise ValueError(f"不支持的执行模式: {chain.execution_mode}")
            
            logger.info(f"工具链执行完成: {chain_name} (ID: {chain_id})")
            return result
            
        except Exception as e:
            logger.error(f"工具链执行失败: {chain_name} (ID: {chain_id}), 错误: {e}")
            return {
                "success": False,
                "error": str(e),
                "chain_id": chain_id,
                "completed_steps": context.current_step,
                "step_results": {k: self._serialize_result(v) for k, v in context.step_results.items()}
            }
        
        finally:
            # 清理执行上下文
            if chain_id in self._running_chains:
                del self._running_chains[chain_id]
    
    async def _execute_sequential(
        self,
        chain: ChainDefinition,
        context: ChainExecutionContext
    ) -> Dict[str, Any]:
        """顺序执行工具链"""
        for i, step in enumerate(chain.steps):
            context.current_step = i
            
            # 检查执行条件
            if step.condition and not self._evaluate_condition(step.condition, context):
                logger.debug(f"跳过步骤 {i}: 条件不满足")
                continue
            
            # 准备参数
            parameters = self._prepare_parameters(step.parameters, context)
            
            # 执行工具
            try:
                result = await self.executor.execute_tool(
                    tool_id=step.tool_id,
                    parameters=parameters
                )
                
                context.step_results[i] = result
                
                # 处理输出映射
                if step.output_mapping and result.success:
                    self._apply_output_mapping(step.output_mapping, result.data, context)
                
                # 处理执行失败
                if not result.success:
                    if step.error_handling == "stop":
                        context.is_failed = True
                        context.error_message = result.error
                        break
                    elif step.error_handling == "continue":
                        logger.warning(f"步骤 {i} 执行失败，继续执行: {result.error}")
                        continue
                    elif step.error_handling == "retry":
                        # 简单重试逻辑
                        retry_result = await self.executor.execute_tool(
                            tool_id=step.tool_id,
                            parameters=parameters
                        )
                        context.step_results[i] = retry_result
                        if not retry_result.success and step.error_handling == "stop":
                            context.is_failed = True
                            context.error_message = retry_result.error
                            break
                
            except Exception as e:
                logger.error(f"步骤 {i} 执行异常: {e}")
                if step.error_handling == "stop":
                    context.is_failed = True
                    context.error_message = str(e)
                    break
        
        context.is_completed = not context.is_failed
        
        return {
            "success": context.is_completed,
            "error": context.error_message,
            "chain_id": context.chain_id,
            "completed_steps": context.current_step + 1,
            "total_steps": len(chain.steps),
            "final_variables": context.variables,
            "step_results": {k: self._serialize_result(v) for k, v in context.step_results.items()}
        }
    
    async def _execute_parallel(
        self,
        chain: ChainDefinition,
        context: ChainExecutionContext
    ) -> Dict[str, Any]:
        """并行执行工具链"""
        # 准备所有步骤的执行配置
        execution_configs = []
        
        for i, step in enumerate(chain.steps):
            # 检查执行条件
            if step.condition and not self._evaluate_condition(step.condition, context):
                continue
            
            parameters = self._prepare_parameters(step.parameters, context)
            execution_configs.append({
                "step_index": i,
                "tool_id": step.tool_id,
                "parameters": parameters
            })
        
        # 并行执行所有步骤
        try:
            results = await self.executor.execute_tools_batch(execution_configs)
            
            # 处理结果
            for i, result in enumerate(results):
                step_index = execution_configs[i]["step_index"]
                context.step_results[step_index] = result
                
                # 处理输出映射
                step = chain.steps[step_index]
                if step.output_mapping and result.success:
                    self._apply_output_mapping(step.output_mapping, result.data, context)
            
            # 检查是否有失败的步骤
            failed_steps = [i for i, result in context.step_results.items() if not result.success]
            
            context.is_completed = len(failed_steps) == 0
            if failed_steps:
                context.error_message = f"步骤 {failed_steps} 执行失败"
            
        except Exception as e:
            context.is_failed = True
            context.error_message = str(e)
        
        return {
            "success": context.is_completed,
            "error": context.error_message,
            "chain_id": context.chain_id,
            "completed_steps": len(context.step_results),
            "total_steps": len(chain.steps),
            "final_variables": context.variables,
            "step_results": {k: self._serialize_result(v) for k, v in context.step_results.items()}
        }
    
    async def _execute_conditional(
        self,
        chain: ChainDefinition,
        context: ChainExecutionContext
    ) -> Dict[str, Any]:
        """条件执行工具链"""
        # 条件执行类似于顺序执行，但更严格地检查条件
        return await self._execute_sequential(chain, context)
    
    def _validate_chain(self, chain: ChainDefinition) -> tuple[bool, Optional[str]]:
        """验证工具链定义
        
        Args:
            chain: 工具链定义
            
        Returns:
            (是否有效, 错误信息)
        """
        if not chain.name:
            return False, "工具链名称不能为空"
        
        if not chain.steps:
            return False, "工具链必须包含至少一个步骤"
        
        for i, step in enumerate(chain.steps):
            if not step.tool_id:
                return False, f"步骤 {i} 缺少工具ID"
            
            if step.error_handling not in ["stop", "continue", "retry"]:
                return False, f"步骤 {i} 错误处理策略无效: {step.error_handling}"
        
        return True, None
    
    def _prepare_parameters(
        self,
        parameters: Dict[str, Any],
        context: ChainExecutionContext
    ) -> Dict[str, Any]:
        """准备参数（支持变量替换）
        
        Args:
            parameters: 原始参数
            context: 执行上下文
            
        Returns:
            处理后的参数
        """
        prepared = {}
        
        for key, value in parameters.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                # 变量替换
                var_name = value[2:-1]
                if var_name in context.variables:
                    prepared[key] = context.variables[var_name]
                else:
                    prepared[key] = value  # 保持原值
            else:
                prepared[key] = value
        
        return prepared
    
    def _evaluate_condition(
        self,
        condition: str,
        context: ChainExecutionContext
    ) -> bool:
        """评估执行条件
        
        Args:
            condition: 条件表达式
            context: 执行上下文
            
        Returns:
            条件是否满足
        """
        try:
            # 简单的条件评估（可以扩展为更复杂的表达式解析）
            # 支持格式：variable == value, variable != value, variable > value 等
            
            if "==" in condition:
                var_name, expected_value = condition.split("==", 1)
                var_name = var_name.strip()
                expected_value = expected_value.strip().strip('"\'')
                
                return str(context.variables.get(var_name, "")) == expected_value
            
            elif "!=" in condition:
                var_name, expected_value = condition.split("!=", 1)
                var_name = var_name.strip()
                expected_value = expected_value.strip().strip('"\'')
                
                return str(context.variables.get(var_name, "")) != expected_value
            
            elif condition in context.variables:
                # 简单的布尔检查
                return bool(context.variables[condition])
            
            else:
                # 默认为真
                return True
                
        except Exception as e:
            logger.error(f"条件评估失败: {condition}, 错误: {e}")
            return False
    
    def _apply_output_mapping(
        self,
        mapping: Dict[str, str],
        output_data: Any,
        context: ChainExecutionContext
    ):
        """应用输出映射
        
        Args:
            mapping: 输出映射配置
            output_data: 输出数据
            context: 执行上下文
        """
        try:
            if isinstance(output_data, dict):
                for source_key, target_var in mapping.items():
                    if source_key in output_data:
                        context.variables[target_var] = output_data[source_key]
            else:
                # 如果输出不是字典，将整个输出映射到指定变量
                if "result" in mapping:
                    context.variables[mapping["result"]] = output_data
                    
        except Exception as e:
            logger.error(f"输出映射失败: {e}")
    
    def _serialize_result(self, result: ToolResult) -> Dict[str, Any]:
        """序列化工具结果
        
        Args:
            result: 工具结果
            
        Returns:
            序列化的结果
        """
        return {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "error_code": result.error_code,
            "execution_time": result.execution_time,
            "token_usage": result.token_usage,
            "metadata": result.metadata
        }
    
    def get_running_chains(self) -> List[Dict[str, Any]]:
        """获取正在运行的工具链
        
        Returns:
            运行中的工具链列表
        """
        chains = []
        for chain_id, context in self._running_chains.items():
            chains.append({
                "chain_id": chain_id,
                "current_step": context.current_step,
                "is_completed": context.is_completed,
                "is_failed": context.is_failed,
                "variables_count": len(context.variables),
                "completed_steps": len(context.step_results)
            })
        
        return chains