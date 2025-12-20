"""
工作流节点基类

定义节点的基本接口和通用功能。
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, TypedDict, Annotated
from operator import add
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage

from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class WorkflowState(TypedDict):
    """工作流状态
    
    在节点间传递的状态对象，包含消息、变量、节点输出等信息。
    """
    # 消息列表（追加模式）
    messages: Annotated[list[AnyMessage], add]
    
    # 输入变量（从配置的 variables 传入）
    variables: dict[str, Any]
    
    # 节点输出（存储每个节点的执行结果，用于变量引用）
    # 使用自定义合并函数，将新的节点输出合并到现有字典中
    node_outputs: Annotated[dict[str, Any], lambda x, y: {**x, **y}]
    
    # 运行时节点变量（简化版，只存储业务数据，供节点间快速访问）
    # 格式：{node_id: business_result}
    runtime_vars: Annotated[dict[str, Any], lambda x, y: {**x, **y}]
    
    # 执行上下文
    execution_id: str
    workspace_id: str
    user_id: str
    
    # 错误信息（用于错误边）
    error: str | None
    error_node: str | None


class BaseNode(ABC):
    """节点基类
    
    所有节点类型都应该继承此基类，实现 execute 方法。
    """
    
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        """初始化节点
        
        Args:
            node_config: 节点配置
            workflow_config: 工作流配置
        """
        self.node_config = node_config
        self.workflow_config = workflow_config
        self.node_id = node_config["id"]
        self.node_type = node_config["type"]
        self.node_name = node_config.get("name", self.node_id)
        # 使用 or 运算符处理 None 值
        self.config = node_config.get("config") or {}
        self.error_handling = node_config.get("error_handling") or {}
    
    @abstractmethod
    async def execute(self, state: WorkflowState) -> Any:
        """执行节点业务逻辑（非流式）
        
        节点只需要返回业务结果，不需要关心输出格式、时间统计等。
        BaseNode 会自动包装成标准格式。
        
        Args:
            state: 工作流状态
        
        Returns:
            业务结果（任意类型）
        
        Examples:
            >>> # LLM 节点
            >>> return "这是 AI 的回复"
            
            >>> # Transform 节点
            >>> return {"processed_data": [...]}
            
            >>> # Start/End 节点
            >>> return {"message": "开始", "conversation_id": "xxx"}
        """
        pass
    
    async def execute_stream(self, state: WorkflowState):
        """执行节点业务逻辑（流式）
        
        子类可以重写此方法以支持流式输出。
        默认实现：执行非流式方法并一次性返回。
        
        节点需要：
        1. yield 中间结果（如文本片段）
        2. 最后 yield 一个特殊的完成标记：{"__final__": True, "result": final_result}
        
        Args:
            state: 工作流状态
        
        Yields:
            业务数据（chunk）或完成标记
        
        Examples:
            >>> # 流式 LLM 节点
            >>> full_response = ""
            >>> async for chunk in llm.astream(prompt):
            ...     full_response += chunk
            ...     yield chunk  # yield 文本片段
            >>> 
            >>> # 最后 yield 完成标记
            >>> yield {"__final__": True, "result": AIMessage(content=full_response)}
        """
        result = await self.execute(state)
        # 默认实现：直接 yield 完成标记
        yield {"__final__": True, "result": result}
    
    def supports_streaming(self) -> bool:
        """节点是否支持流式输出
        
        Returns:
            是否支持流式输出
        """
        # 检查子类是否重写了 execute_stream 方法
        return self.execute_stream.__func__ != BaseNode.execute_stream.__func__
    
    def get_timeout(self) -> int:
        """获取超时时间（秒）
        
        Returns:
            超时时间
        """
        return 60
        # return self.error_handling.get("timeout", 60)
    
    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """执行节点（带错误处理和输出包装，非流式）
        
        这个方法由 Executor 调用，负责：
        1. 时间统计
        2. 调用节点的 execute() 方法
        3. 将业务结果包装成标准输出格式
        4. 错误处理
        
        Args:
            state: 工作流状态
        
        Returns:
            标准化的状态更新字典
        """
        import time
        
        start_time = time.time()
        
        try:
            timeout = self.get_timeout()
            
            # 调用节点的业务逻辑
            business_result = await asyncio.wait_for(
                self.execute(state),
                timeout=timeout
            )
            
            elapsed_time = time.time() - start_time
            
            # 提取处理后的输出（调用子类的 _extract_output）
            extracted_output = self._extract_output(business_result)
            
            # 包装成标准输出格式
            wrapped_output = self._wrap_output(business_result, elapsed_time, state)
            
            # 将提取后的输出存储到运行时变量中（供后续节点快速访问）
            # 如果提取后的输出是字典，拆包存储；否则存储为 output 字段
            if isinstance(extracted_output, dict):
                runtime_var = extracted_output
            else:
                runtime_var = {"output": extracted_output}
            
            # 返回包装后的输出和运行时变量
            return {
                **wrapped_output,
                "runtime_vars": {
                    self.node_id: runtime_var
                }
            }
            
        except TimeoutError:
            elapsed_time = time.time() - start_time
            logger.error(f"节点 {self.node_id} 执行超时（{timeout}秒）")
            return self._wrap_error(f"节点执行超时（{timeout}秒）", elapsed_time, state)
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"节点 {self.node_id} 执行失败: {e}", exc_info=True)
            return self._wrap_error(str(e), elapsed_time, state)
    
    async def run_stream(self, state: WorkflowState):
        """执行节点（带错误处理和输出包装，流式）
        
        这个方法由 Executor 调用，负责：
        1. 时间统计
        2. 调用节点的 execute_stream() 方法
        3. 将业务数据包装成标准输出格式
        4. 错误处理
        
        注意：在流式模式下，我们需要：
        - yield 中间的 chunk 事件（用于实时显示）
        - 最后 yield 一个包含 state 更新的字典（LangGraph 会合并到 state）
        
        Args:
            state: 工作流状态
        
        Yields:
            标准化的流式事件和最终的 state 更新
        """
        import time
        
        start_time = time.time()
        
        try:
            timeout = self.get_timeout()
            
            # 累积完整结果（用于最后的包装）
            chunks = []
            final_result = None
            
            # 使用异步生成器包装，支持超时
            async def stream_with_timeout():
                nonlocal final_result
                loop_start = asyncio.get_event_loop().time()
                
                async for item in self.execute_stream(state):
                    # 检查超时
                    if asyncio.get_event_loop().time() - loop_start > timeout:
                        raise TimeoutError()
                    
                    # 检查是否是完成标记
                    if isinstance(item, dict) and item.get("__final__"):
                        final_result = item["result"]
                    elif isinstance(item, str):
                        # 字符串是 chunk
                        # print("="*50)
                        # print(item)
                        # print("-"*50)
                        chunks.append(item)
                        yield {
                            "type": "chunk",
                            "node_id": self.node_id,
                            "content": item,
                            "full_content": "".join(chunks)
                        }
                    else:
                        # 其他类型也当作 chunk 处理
                        chunks.append(str(item))
                        yield {
                            "type": "chunk",
                            "node_id": self.node_id,
                            "content": str(item),
                            "full_content": "".join(chunks)
                        }
            
            async for chunk_event in stream_with_timeout():
                yield chunk_event
            
            elapsed_time = time.time() - start_time
            
            # 提取处理后的输出（调用子类的 _extract_output）
            extracted_output = self._extract_output(final_result)
            
            # 包装最终结果
            final_output = self._wrap_output(final_result, elapsed_time, state)
            
            # 将提取后的输出存储到运行时变量中（供后续节点快速访问）
            if isinstance(extracted_output, dict):
                runtime_var = extracted_output
            else:
                runtime_var = {"output": extracted_output}
            
            # 构建完整的 state 更新（包含 node_outputs 和 runtime_vars）
            state_update = {
                **final_output,
                "runtime_vars": {
                    self.node_id: runtime_var
                }
            }
            
            # 最后 yield 纯粹的 state 更新（LangGraph 会合并到 state 中）
            yield state_update
                
        except TimeoutError:
            elapsed_time = time.time() - start_time
            logger.error(f"节点 {self.node_id} 执行超时（{timeout}秒）")
            error_output = self._wrap_error(f"节点执行超时（{timeout}秒）", elapsed_time, state)
            yield error_output
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"节点 {self.node_id} 执行失败: {e}", exc_info=True)
            error_output = self._wrap_error(str(e), elapsed_time, state)
            yield error_output
    
    def _wrap_output(
        self, 
        business_result: Any, 
        elapsed_time: float,
        state: WorkflowState
    ) -> dict[str, Any]:
        """将业务结果包装成标准输出格式
        
        Args:
            business_result: 节点返回的业务结果
            elapsed_time: 执行耗时
            state: 工作流状态
        
        Returns:
            标准化的状态更新字典
        """
        # 提取输入数据（用于记录）
        input_data = self._extract_input(state)
        
        # 提取 token 使用情况（如果有）
        token_usage = self._extract_token_usage(business_result)
        
        # 提取实际输出（去除元数据）
        output = self._extract_output(business_result)
        
        # 构建标准节点输出
        node_output = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "node_name": self.node_name,
            "status": "completed",
            "input": input_data,
            "output": output,
            "elapsed_time": elapsed_time,
            "token_usage": token_usage,
            "error": None
        }
        
        return {
            "node_outputs": {
                self.node_id: node_output
            }
        }
    
    def _wrap_error(
        self, 
        error_message: str, 
        elapsed_time: float,
        state: WorkflowState
    ) -> dict[str, Any]:
        """将错误包装成标准输出格式
        
        Args:
            error_message: 错误信息
            elapsed_time: 执行耗时
            state: 工作流状态
        
        Returns:
            标准化的状态更新字典
        """
        # 查找错误边
        error_edge = self._find_error_edge()
        
        # 提取输入数据
        input_data = self._extract_input(state)
        
        # 构建错误输出
        node_output = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "node_name": self.node_name,
            "status": "failed",
            "input": input_data,
            "output": None,
            "elapsed_time": elapsed_time,
            "token_usage": None,
            "error": error_message
        }
        
        if error_edge:
            # 有错误边：记录错误并继续
            logger.warning(
                f"节点 {self.node_id} 执行失败，跳转到错误处理节点: {error_edge['target']}"
            )
            return {
                "node_outputs": {
                    self.node_id: node_output
                },
                "error": error_message,
                "error_node": self.node_id
            }
        else:
            # 无错误边：抛出异常停止工作流
            logger.error(f"节点 {self.node_id} 执行失败，停止工作流: {error_message}")
            raise Exception(f"节点 {self.node_id} 执行失败: {error_message}")
    
    def _extract_input(self, state: WorkflowState) -> dict[str, Any]:
        """提取节点输入数据（用于记录）
        
        子类可以重写此方法来自定义输入记录。
        
        Args:
            state: 工作流状态
        
        Returns:
            输入数据字典
        """
        # 默认返回配置
        return {"config": self.config}
    
    def _extract_output(self, business_result: Any) -> Any:
        """从业务结果中提取实际输出
        
        子类可以重写此方法来自定义输出提取。
        
        Args:
            business_result: 业务结果
        
        Returns:
            实际输出
        """
        # 默认直接返回业务结果
        return business_result
    
    def _extract_token_usage(self, business_result: Any) -> dict[str, int] | None:
        """从业务结果中提取 token 使用情况
        
        子类可以重写此方法来提取 token 信息。
        
        Args:
            business_result: 业务结果
        
        Returns:
            token 使用情况或 None
        """
        # 默认返回 None
        return None
    
    def _find_error_edge(self) -> dict[str, Any] | None:
        """查找错误边
        
        Returns:
            错误边配置或 None
        """
        for edge in self.workflow_config.get("edges", []):
            if edge.get("source") == self.node_id and edge.get("type") == "error":
                return edge
        return None
    
    def _render_template(self, template: str, state: WorkflowState | None) -> str:
        """渲染模板
        
        支持的变量命名空间：
        - sys.xxx: 系统变量（message, execution_id, workspace_id, user_id, conversation_id）
        - conv.xxx: 会话变量（跨多轮对话保持）
        - node_id.xxx: 节点输出
        
        Args:
            template: 模板字符串
            state: 工作流状态
        
        Returns:
            渲染后的字符串
        """
        from app.core.workflow.template_renderer import render_template
        
        # 处理 state 为 None 的情况
        if state is None:
            state = {}
        
        # 使用变量池获取变量
        pool = VariablePool(state)
        
        return render_template(
            template=template,
            variables=pool.get_all_conversation_vars(),
            node_outputs=pool.get_all_node_outputs(),
            system_vars=pool.get_all_system_vars()
        )
    
    def _evaluate_condition(self, expression: str, state: WorkflowState | None) -> bool:
        """评估条件表达式
        
        支持的变量命名空间：
        - sys.xxx: 系统变量
        - conv.xxx: 会话变量
        - node_id.xxx: 节点输出
        
        Args:
            expression: 条件表达式
            state: 工作流状态
        
        Returns:
            布尔值结果
        """
        from app.core.workflow.expression_evaluator import evaluate_condition
        
        # 处理 state 为 None 的情况
        if state is None:
            state = {}
        
        # 使用变量池获取变量
        pool = VariablePool(state)
        
        return evaluate_condition(
            expression=expression,
            variables=pool.get_all_conversation_vars(),
            node_outputs=pool.get_all_node_outputs(),
            system_vars=pool.get_all_system_vars()
        )

    def get_variable_pool(self, state: WorkflowState) -> VariablePool:
        """获取变量池实例
        
        VariablePool 是轻量级包装器，只持有 state 的引用，创建成本极低。
        
        Args:
            state: 工作流状态
        
        Returns:
            VariablePool 实例
        
        Examples:
            >>> pool = self.get_variable_pool(state)
            >>> message = pool.get("sys.message")
            >>> llm_output = pool.get("llm_qa.output")
        """
        return VariablePool(state)
    
    def get_variable(
        self, 
        selector: list[str] | str, 
        state: WorkflowState,
        default: Any = None
    ) -> Any:
        """获取变量值（便捷方法）
        
        Args:
            selector: 变量选择器
            state: 工作流状态
            default: 默认值
        
        Returns:
            变量值
        
        Examples:
            >>> message = self.get_variable("sys.message", state)
            >>> output = self.get_variable(["llm_qa", "output"], state)
            >>> custom = self.get_variable("var.custom", state, default="默认值")
        """
        pool = VariablePool(state)
        return pool.get(selector, default=default)
    
    def has_variable(self, selector: list[str] | str, state: WorkflowState) -> bool:
        """检查变量是否存在（便捷方法）
        
        Args:
            selector: 变量选择器
            state: 工作流状态
        
        Returns:
            变量是否存在
        
        Examples:
            >>> if self.has_variable("llm_qa.output", state):
            ...     output = self.get_variable("llm_qa.output", state)
        """
        pool = VariablePool(state)
        return pool.has(selector)
