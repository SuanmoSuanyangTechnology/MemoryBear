"""
工作流执行器

基于 LangGraph 的工作流执行引擎。
"""

import logging
import uuid
import datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.nodes import WorkflowState, NodeFactory
from app.core.workflow.expression_evaluator import evaluate_condition
from app.core.tools.registry import ToolRegistry
from app.core.tools.executor import ToolExecutor
from app.core.tools.langchain_adapter import LangchainAdapter
TOOL_MANAGEMENT_AVAILABLE = True

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """工作流执行器

    负责将工作流配置转换为 LangGraph 并执行。
    """

    def __init__(
        self,
        workflow_config: dict[str, Any],
        execution_id: str,
        workspace_id: str,
        user_id: str
    ):
        """初始化执行器

        Args:
            workflow_config: 工作流配置
            execution_id: 执行 ID
            workspace_id: 工作空间 ID
            user_id: 用户 ID
        """
        self.workflow_config = workflow_config
        self.execution_id = execution_id
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.nodes = workflow_config.get("nodes", [])
        self.edges = workflow_config.get("edges", [])
        self.execution_config = workflow_config.get("execution_config", {})

    def _prepare_initial_state(self, input_data: dict[str, Any]) -> WorkflowState:
        """准备初始状态（注入系统变量和会话变量）

        变量命名空间：
        - sys.xxx - 系统变量（execution_id, workspace_id, user_id, message, input_variables 等）
        - conv.xxx - 会话变量（跨多轮对话保持）
        - node_id.xxx - 节点输出（执行时动态生成）

        Args:
            input_data: 输入数据

        Returns:
            初始化的工作流状态
        """
        user_message = input_data.get("message") or ""
        conversation_vars = input_data.get("conversation_vars") or {}
        input_variables = input_data.get("variables") or {}  # Start 节点的自定义变量

        # 构建分层的变量结构
        variables = {
            "sys": {
                "message": user_message,                          # 用户消息
                "conversation_id": input_data.get("conversation_id"),  # 会话 ID
                "execution_id": self.execution_id,                # 执行 ID
                "workspace_id": self.workspace_id,                # 工作空间 ID
                "user_id": self.user_id,                          # 用户 ID
                "input_variables": input_variables,               # 自定义输入变量（给 Start 节点使用）
            },
            "conv": conversation_vars  # 会话级变量（跨多轮对话保持）
        }

        return {
            "messages": [HumanMessage(content=user_message)],
            "variables": variables,
            "node_outputs": {},
            "runtime_vars": {},  # 运行时节点变量（简化版，供快速访问）
            "execution_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "error": None,
            "error_node": None
        }



    def build_graph(self) -> CompiledStateGraph:
        """构建 LangGraph

        Returns:
            编译后的状态图
        """
        logger.info(f"开始构建工作流图: execution_id={self.execution_id}")

        # 1. 创建状态图
        workflow = StateGraph(WorkflowState)

        # 2. 添加所有节点（包括 start 和 end）
        start_node_id = None
        end_node_ids = []

        for node in self.nodes:
            node_type = node.get("type")
            node_id = node.get("id")

            # 记录 start 和 end 节点 ID
            if node_type == "start":
                start_node_id = node_id
            elif node_type == "end":
                end_node_ids.append(node_id)

            # 创建节点实例（现在 start 和 end 也会被创建）
            node_instance = NodeFactory.create_node(node, self.workflow_config)
            if node_instance:
                # 包装节点的 run 方法
                # 使用函数工厂避免闭包问题
                def make_node_func(inst):
                    async def node_func(state: WorkflowState):
                        return await inst.run(state)
                    return node_func

                workflow.add_node(node_id, make_node_func(node_instance))
                logger.debug(f"添加节点: {node_id} (type={node_type})")

        # 3. 添加边
        # 从 START 连接到 start 节点
        if start_node_id:
            workflow.add_edge(START, start_node_id)
            logger.debug(f"添加边: START -> {start_node_id}")

        for edge in self.edges:
            source = edge.get("source")
            target = edge.get("target")
            edge_type = edge.get("type")
            condition = edge.get("condition")

            # 跳过从 start 节点出发的边（因为已经从 START 连接到 start）
            if source == start_node_id:
                # 但要连接 start 到下一个节点
                workflow.add_edge(source, target)
                logger.debug(f"添加边: {source} -> {target}")
                continue

            # 处理到 end 节点的边
            if target in end_node_ids:
                # 连接到 end 节点
                workflow.add_edge(source, target)
                logger.debug(f"添加边: {source} -> {target}")
                continue

            # 跳过错误边（在节点内部处理）
            if edge_type == "error":
                continue

            if condition:
                # 条件边
                def router(state: WorkflowState, cond=condition, tgt=target):
                    """条件路由函数"""
                    if evaluate_condition(
                        cond,
                        state.get("variables", {}),
                        state.get("node_outputs", {}),
                        {
                            "execution_id": state.get("execution_id"),
                            "workspace_id": state.get("workspace_id"),
                            "user_id": state.get("user_id")
                        }
                    ):
                        return tgt
                    return END  # 条件不满足，结束

                workflow.add_conditional_edges(source, router)
                logger.debug(f"添加条件边: {source} -> {target} (condition={condition})")
            else:
                # 普通边
                workflow.add_edge(source, target)
                logger.debug(f"添加边: {source} -> {target}")

        # 从 end 节点连接到 END
        for end_node_id in end_node_ids:
            workflow.add_edge(end_node_id, END)
            logger.debug(f"添加边: {end_node_id} -> END")

        # 4. 编译图
        graph = workflow.compile()
        logger.info(f"工作流图构建完成: execution_id={self.execution_id}")

        return graph

    async def execute(
        self,
        input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """执行工作流（非流式）

        Args:
            input_data: 输入数据，包含 message 和 variables

        Returns:
            执行结果，包含 status, output, node_outputs, elapsed_time, token_usage
        """
        logger.info(f"开始执行工作流: execution_id={self.execution_id}")

        # 记录开始时间
        start_time = datetime.datetime.now()

        # 1. 构建图
        graph = self.build_graph()

        # 2. 初始化状态（自动注入系统变量）
        initial_state = self._prepare_initial_state(input_data)

        # 3. 执行工作流
        try:
            result = await graph.ainvoke(initial_state)

            # 计算耗时
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            # 提取节点输出（现在包含 start 和 end 节点）
            node_outputs = result.get("node_outputs", {})

            # 提取最终输出（从最后一个非 start/end 节点）
            final_output = self._extract_final_output(node_outputs)

            # 聚合 token 使用情况
            token_usage = self._aggregate_token_usage(node_outputs)

            # 提取 conversation_id（从 start 节点输出）
            conversation_id = None
            for node_id, node_output in node_outputs.items():
                if node_output.get("node_type") == "start":
                    conversation_id = node_output.get("output", {}).get("conversation_id")
                    break

            logger.info(f"工作流执行完成: execution_id={self.execution_id}, elapsed_time={elapsed_time:.2f}s")

            return {
                "status": "completed",
                "output": final_output,
                "node_outputs": node_outputs,
                "messages": result.get("messages", []),
                "conversation_id": conversation_id,
                "elapsed_time": elapsed_time,
                "token_usage": token_usage,
                "error": result.get("error")
            }

        except Exception as e:
            # 计算耗时（即使失败也记录）
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.error(f"工作流执行失败: execution_id={self.execution_id}, error={e}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "output": None,
                "node_outputs": {},
                "elapsed_time": elapsed_time,
                "token_usage": None
            }

    async def execute_stream(
        self,
        input_data: dict[str, Any]
    ):
        """执行工作流（流式）

        手动执行节点以支持细粒度的流式输出：
        - workflow_start: 工作流开始
        - node_start: 节点开始执行
        - node_chunk: LLM 节点的流式输出片段（逐 token）
        - node_complete: 节点执行完成
        - workflow_complete: 工作流完成

        Args:
            input_data: 输入数据

        Yields:
            流式事件
        """
        #
        logger.info(f"开始执行工作流: execution_id={self.execution_id}")

        # 记录开始时间
        start_time = datetime.datetime.now()

        # 1. 构建图
        graph = self.build_graph()

        # 2. 初始化状态（自动注入系统变量）
        initial_state = self._prepare_initial_state(input_data)

        # 3. 执行工作流
        try:
            async for chunk in graph.astream(
                    initial_state,
                    # subgraphs=True,
                    stream_mode="updates",
            ):
                # print(chunk)
                yield chunk

        except Exception as e:
            # 计算耗时（即使失败也记录）
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.error(f"工作流执行失败: execution_id={self.execution_id}, error={e}", exc_info=True)
            yield {
                "status": "failed",
                "error": str(e),
                "output": None,
                "node_outputs": {},
                "elapsed_time": elapsed_time,
                "token_usage": None
            }


    def _extract_final_output(self, node_outputs: dict[str, Any]) -> str | None:
        """从节点输出中提取最终输出

        优先级：
        1. 最后一个执行的非 start/end 节点的 output
        2. 如果没有节点输出，返回 None

        Args:
            node_outputs: 所有节点的输出

        Returns:
            最终输出字符串或 None
        """
        if not node_outputs:
            return None

        # 获取最后一个节点的输出
        last_node_output = list(node_outputs.values())[-1] if node_outputs else None

        if last_node_output and isinstance(last_node_output, dict):
            return last_node_output.get("output")

        return None

    def _aggregate_token_usage(self, node_outputs: dict[str, Any]) -> dict[str, int] | None:
        """聚合所有节点的 token 使用情况

        Args:
            node_outputs: 所有节点的输出

        Returns:
            聚合的 token 使用情况 {"prompt_tokens": x, "completion_tokens": y, "total_tokens": z}
            如果没有 token 使用信息，返回 None
        """
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        has_token_info = False

        for node_output in node_outputs.values():
            if isinstance(node_output, dict):
                token_usage = node_output.get("token_usage")
                if token_usage and isinstance(token_usage, dict):
                    has_token_info = True
                    total_prompt_tokens += token_usage.get("prompt_tokens", 0)
                    total_completion_tokens += token_usage.get("completion_tokens", 0)
                    total_tokens += token_usage.get("total_tokens", 0)

        if not has_token_info:
            return None

        return {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens
        }


async def execute_workflow(
    workflow_config: dict[str, Any],
    input_data: dict[str, Any],
    execution_id: str,
    workspace_id: str,
    user_id: str
) -> dict[str, Any]:
    """执行工作流（便捷函数）

    Args:
        workflow_config: 工作流配置
        input_data: 输入数据
        execution_id: 执行 ID
        workspace_id: 工作空间 ID
        user_id: 用户 ID

    Returns:
        执行结果
    """
    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        execution_id=execution_id,
        workspace_id=workspace_id,
        user_id=user_id
    )
    return await executor.execute(input_data)


async def execute_workflow_stream(
    workflow_config: dict[str, Any],
    input_data: dict[str, Any],
    execution_id: str,
    workspace_id: str,
    user_id: str
):
    """执行工作流（流式，便捷函数）

    Args:
        workflow_config: 工作流配置
        input_data: 输入数据
        execution_id: 执行 ID
        workspace_id: 工作空间 ID
        user_id: 用户 ID

    Yields:
        流式事件
    """
    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        execution_id=execution_id,
        workspace_id=workspace_id,
        user_id=user_id
    )
    async for event in executor.execute_stream(input_data):
        yield event


# ==================== 工具管理系统集成 ====================

def get_workflow_tools(workspace_id: str, user_id: str) -> list:
    """获取工作流可用的工具列表

    Args:
        workspace_id: 工作空间ID
        user_id: 用户ID

    Returns:
        可用工具列表
    """
    if not TOOL_MANAGEMENT_AVAILABLE:
        logger.warning("工具管理系统不可用")
        return []

    try:
        from sqlalchemy.orm import Session
        db = next(get_db())

        # 创建工具注册表
        registry = ToolRegistry(db)

        # 注册内置工具类
        from app.core.tools.builtin import (
            DateTimeTool, JsonTool, BaiduSearchTool, MinerUTool, TextInTool
        )
        registry.register_tool_class(DateTimeTool)
        registry.register_tool_class(JsonTool)
        registry.register_tool_class(BaiduSearchTool)
        registry.register_tool_class(MinerUTool)
        registry.register_tool_class(TextInTool)

        # 获取活跃的工具
        import uuid
        tools = registry.list_tools(workspace_id=uuid.UUID(workspace_id))
        active_tools = [tool for tool in tools if tool.status.value == "active"]

        # 转换为Langchain工具
        langchain_tools = []
        for tool_info in active_tools:
            try:
                tool_instance = registry.get_tool(tool_info.id)
                if tool_instance:
                    langchain_tool = LangchainAdapter.convert_tool(tool_instance)
                    langchain_tools.append(langchain_tool)
            except Exception as e:
                logger.error(f"转换工具失败: {tool_info.name}, 错误: {e}")

        logger.info(f"为工作流获取了 {len(langchain_tools)} 个工具")
        return langchain_tools

    except Exception as e:
        logger.error(f"获取工作流工具失败: {e}")
        return []


class ToolWorkflowNode:
    """工具工作流节点 - 在工作流中执行工具"""

    def __init__(self, node_config: dict, workflow_config: dict):
        """初始化工具节点

        Args:
            node_config: 节点配置
            workflow_config: 工作流配置
        """
        self.node_config = node_config
        self.workflow_config = workflow_config
        self.tool_id = node_config.get("tool_id")
        self.tool_parameters = node_config.get("parameters", {})

    async def run(self, state: WorkflowState) -> WorkflowState:
        """执行工具节点"""
        if not TOOL_MANAGEMENT_AVAILABLE:
            logger.error("工具管理系统不可用")
            state["error"] = "工具管理系统不可用"
            return state

        try:
            from sqlalchemy.orm import Session
            db = next(get_db())

            # 创建工具执行器
            registry = ToolRegistry(db)
            executor = ToolExecutor(db, registry)

            # 准备参数（支持变量替换）
            parameters = self._prepare_parameters(state)

            # 执行工具
            result = await executor.execute_tool(
                tool_id=self.tool_id,
                parameters=parameters,
                user_id=uuid.UUID(state["user_id"]),
                workspace_id=uuid.UUID(state["workspace_id"])
            )

            # 更新状态
            node_id = self.node_config.get("id")
            if result.success:
                state["node_outputs"][node_id] = {
                    "type": "tool",
                    "tool_id": self.tool_id,
                    "output": result.data,
                    "execution_time": result.execution_time,
                    "token_usage": result.token_usage
                }

                # 更新运行时变量
                if isinstance(result.data, dict):
                    for key, value in result.data.items():
                        state["runtime_vars"][f"{node_id}.{key}"] = value
                else:
                    state["runtime_vars"][f"{node_id}.result"] = result.data
            else:
                state["error"] = result.error
                state["error_node"] = node_id
                state["node_outputs"][node_id] = {
                    "type": "tool",
                    "tool_id": self.tool_id,
                    "error": result.error,
                    "execution_time": result.execution_time
                }

            return state

        except Exception as e:
            logger.error(f"工具节点执行失败: {e}")
            state["error"] = str(e)
            state["error_node"] = self.node_config.get("id")
            return state

    def _prepare_parameters(self, state: WorkflowState) -> dict:
        """准备工具参数（支持变量替换）"""
        parameters = {}

        for key, value in self.tool_parameters.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                # 变量替换
                var_path = value[2:-1]

                # 支持多层级变量访问，如 ${sys.message} 或 ${node1.result}
                if "." in var_path:
                    parts = var_path.split(".")
                    current = state.get("variables", {})

                    for part in parts:
                        if isinstance(current, dict) and part in current:
                            current = current[part]
                        else:
                            # 尝试从运行时变量获取
                            runtime_key = ".".join(parts)
                            current = state.get("runtime_vars", {}).get(runtime_key, value)
                            break

                    parameters[key] = current
                else:
                    # 简单变量
                    variables = state.get("variables", {})
                    parameters[key] = variables.get(var_path, value)
            else:
                parameters[key] = value

        return parameters


# 注册工具节点到NodeFactory（如果存在）
try:
    from app.core.workflow.nodes import NodeFactory
    if hasattr(NodeFactory, 'register_node_type'):
        NodeFactory.register_node_type("tool", ToolWorkflowNode)
    logger.info("工具节点已注册到工作流系统")
except Exception as e:
    logger.warning(f"注册工具节点失败: {e}")