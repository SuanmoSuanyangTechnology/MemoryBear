"""
工作流执行器

基于 LangGraph 的工作流执行引擎。
"""
import datetime
import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.graph_builder import GraphBuilder
from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_config import VariableType
from app.core.workflow.nodes.enums import NodeType

# from app.core.tools.registry import ToolRegistry
# from app.core.tools.executor import ToolExecutor
# from app.core.tools.langchain_adapter import LangchainAdapter
# TOOL_MANAGEMENT_AVAILABLE = True
# from app.db import get_db

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

        self.start_node_id = None

        self.checkpoint_config = RunnableConfig(
            configurable={
                "thread_id": uuid.uuid4(),
            }
        )

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
        conversation_messages = input_data.get("conv_messages") or []

        # 会话变量处理：从配置文件获取变量定义列表，转换为字典（name -> default value）
        config_variables_list = self.workflow_config.get("variables") or []
        conversation_vars = {}
        for var_def in config_variables_list:
            if isinstance(var_def, dict):
                var_name = var_def.get("name")
                var_default = var_def.get("default")
                if var_name:
                    if var_default:
                        conversation_vars[var_name] = var_default
                    else:
                        var_type = var_def.get("type")
                        match var_type:
                            case VariableType.STRING:
                                conversation_vars[var_name] = ""
                            case VariableType.NUMBER:
                                conversation_vars[var_name] = 0
                            case VariableType.OBJECT:
                                conversation_vars[var_name] = {}
                            case VariableType.BOOLEAN:
                                conversation_vars[var_name] = False
                            case VariableType.ARRAY_NUMBER | VariableType.ARRAY_OBJECT | VariableType.ARRAY_BOOLEAN | VariableType.ARRAY_STRING:
                                conversation_vars[var_name] = []
        input_variables = input_data.get("variables") or {}  # Start 节点的自定义变量
        conversation_vars = conversation_vars | input_data.get("conv", {})
        # 构建分层的变量结构
        variables = {
            "sys": {
                "message": user_message,  # 用户消息
                "conversation_id": input_data.get("conversation_id"),  # 会话 ID
                "execution_id": self.execution_id,  # 执行 ID
                "workspace_id": self.workspace_id,  # 工作空间 ID
                "user_id": self.user_id,  # 用户 ID
                "input_variables": input_variables,  # 自定义输入变量（给 Start 节点使用）
            },
            "conv": conversation_vars  # 会话级变量（跨多轮对话保持）
        }

        return {
            "messages": conversation_messages,
            "variables": variables,
            "node_outputs": {},
            "runtime_vars": {},  # 运行时节点变量（简化版，供快速访问）
            "execution_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "error": None,
            "error_node": None,
            "streaming_buffer": {},  # 流式缓冲区
            "cycle_nodes": [
                node.get("id")
                for node in self.workflow_config.get("nodes")
                if node.get("type") in [NodeType.LOOP, NodeType.ITERATION]
            ],  # loop, iteration node id
            "looping": 0,  # loop runing flag, only use in loop node,not use in main loop
            "activate": {
                self.start_node_id: True
            }
        }

    def _build_final_output(self, result, elapsed_time):
        node_outputs = result.get("node_outputs", {})
        final_output = self._extract_final_output(node_outputs)
        token_usage = self._aggregate_token_usage(node_outputs)
        conversation_id = None
        for node_id, node_output in node_outputs.items():
            if node_output.get("node_type") == "start":
                conversation_id = node_output.get("output", {}).get("conversation_id")
                break

        return {
            "status": "completed",
            "output": final_output,
            "variables": result.get("variables", {}),
            "node_outputs": node_outputs,
            "messages": result.get("messages", []),
            "conversation_id": conversation_id,
            "elapsed_time": elapsed_time,
            "token_usage": token_usage,
            "error": result.get("error"),
        }

    def build_graph(self, stream=False) -> CompiledStateGraph:
        """构建 LangGraph

        Returns:
            编译后的状态图
        """
        logger.info(f"开始构建工作流图: execution_id={self.execution_id}")
        builder = GraphBuilder(
            self.workflow_config,
            stream=stream,
        )
        self.start_node_id = builder.start_node_id
        graph = builder.build()
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

            result = await graph.ainvoke(initial_state, config=self.checkpoint_config)

            # 计算耗时
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.info(f"工作流执行完成: execution_id={self.execution_id}, elapsed_time={elapsed_time:.2f}s")

            return self._build_final_output(result, elapsed_time)

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

        使用多个 stream_mode 来获取：
        1. "updates" - 节点的 state 更新和流式 chunk
        2. "debug" - 节点执行的详细信息（开始/完成时间）
        3. "custom" - 自定义流式数据（chunks）

        Args:
            input_data: 输入数据

        Yields:
            流式事件，格式：
            {
                "event": "workflow_start" | "workflow_end" | "node_start" | "node_end" | "node_chunk" | "message",
                "data": {...}
            }
        """
        logger.info(f"开始执行工作流（流式）: execution_id={self.execution_id}")

        # 记录开始时间
        start_time = datetime.datetime.now()

        # 发送 workflow_start 事件
        yield {
            "event": "workflow_start",
            "data": {
                "execution_id": self.execution_id,
                "workspace_id": self.workspace_id,
                "timestamp": start_time.isoformat()
            }
        }

        # 1. 构建图
        graph = self.build_graph(stream=True)

        # 2. 初始化状态（自动注入系统变量）
        initial_state = self._prepare_initial_state(input_data)
        # 3. Execute workflow
        try:
            chunk_count = 0

            async for event in graph.astream(
                    initial_state,
                    stream_mode=["updates", "debug", "custom"],  # Use updates + debug + custom mode
                    config=self.checkpoint_config
            ):
                # event should be a tuple: (mode, data)
                # But let's handle both cases
                if isinstance(event, tuple) and len(event) == 2:
                    mode, data = event
                else:
                    # Unexpected format, log and skip
                    logger.warning(f"[STREAM] Unexpected event format: {type(event)}, value: {event}"
                                   f"- execution_id: {self.execution_id}")
                    continue

                if mode == "custom":
                    # Handle custom streaming events (chunks from nodes via stream writer)
                    chunk_count += 1
                    event_type = data.get("type", "node_chunk")  # "message" or "node_chunk"
                    logger.info(f"[CUSTOM] ✅ 收到 {event_type} #{chunk_count} from {data.get('node_id')}"
                                f"- execution_id: {self.execution_id}")
                    yield {
                        "event": event_type,  # "message" or "node_chunk"
                        "data": {
                            "node_id": data.get("node_id"),
                            "chunk": data.get("chunk"),
                            "full_content": data.get("full_content"),
                            "chunk_index": data.get("chunk_index"),
                            "is_prefix": data.get("is_prefix"),
                            "is_suffix": data.get("is_suffix"),
                            "conversation_id": input_data.get("conversation_id"),
                        }
                    }

                elif mode == "debug":
                    # Handle debug information (node execution status)
                    event_type = data.get("type")
                    payload = data.get("payload", {})
                    node_name = payload.get("name")

                    if node_name and node_name.startswith("nop"):
                        continue

                    if event_type == "task":
                        # Node starts execution
                        inputv = payload.get("input", {})
                        if not inputv.get("activate", {}).get(node_name):
                            continue
                        conversation_id = input_data.get("conversation_id")
                        logger.info(f"[NODE-START] Node starts execution: {node_name} "
                                    f"- execution_id: {self.execution_id}")

                        yield {
                            "event": "node_start",
                            "data": {
                                "node_id": node_name,
                                "conversation_id": conversation_id,
                                "execution_id": self.execution_id,
                                "timestamp": data.get("timestamp"),
                            }
                        }
                    elif event_type == "task_result":
                        # Node execution completed
                        result = payload.get("result", {})
                        if not result.get("activate", {}).get(node_name):
                            continue

                        conversation_id = input_data.get("conversation_id")
                        logger.info(f"[NODE-END] Node execution completed: {node_name} "
                                    f"- execution_id: {self.execution_id}")

                        yield {
                            "event": "node_end",
                            "data": {
                                "node_id": node_name,
                                "conversation_id": conversation_id,
                                "execution_id": self.execution_id,
                                "timestamp": data.get("timestamp"),
                                "state": result.get("node_outputs", {}).get(node_name),
                            }
                        }

                elif mode == "updates":
                    # Handle state updates - store final state
                    # TODO:流式输出点
                    logger.debug(f"[UPDATES] 收到 state 更新 from {list(data.keys())} "
                                 f"- execution_id: {self.execution_id}")

            # 计算耗时
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()
            result = graph.get_state(self.checkpoint_config).values
            logger.info(
                f"Workflow execution completed (streaming), "
                f"total chunks: {chunk_count}, elapsed: {elapsed_time:.2f}s, execution_id: {self.execution_id}"
            )

            # 发送 workflow_end 事件
            yield {
                "event": "workflow_end",
                "data": self._build_final_output(result, elapsed_time)
            }

        except Exception as e:
            # 计算耗时（即使失败也记录）
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.error(f"工作流执行失败: execution_id={self.execution_id}, error={e}", exc_info=True)

            # 发送 workflow_end 事件（失败）
            yield {
                "event": "workflow_end",
                "data": {
                    "execution_id": self.execution_id,
                    "status": "failed",
                    "error": str(e),
                    "elapsed_time": elapsed_time,
                    "timestamp": end_time.isoformat()
                }
            }

    @staticmethod
    def _extract_final_output(node_outputs: dict[str, Any]) -> str | None:
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

    @staticmethod
    def _aggregate_token_usage(node_outputs: dict[str, Any]) -> dict[str, int] | None:
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

# def get_workflow_tools(workspace_id: str, user_id: str) -> list:
#     """获取工作流可用的工具列表
#
#     Args:
#         workspace_id: 工作空间ID
#         user_id: 用户ID
#
#     Returns:
#         可用工具列表
#     """
#     if not TOOL_MANAGEMENT_AVAILABLE:
#         logger.warning("工具管理系统不可用")
#         return []
#
#     try:
#         db = next(get_db())
#
#         # 创建工具注册表
#         registry = ToolRegistry(db)
#
#         # 注册内置工具类
#         from app.core.tools.builtin import (
#             DateTimeTool, JsonTool, BaiduSearchTool, MinerUTool, TextInTool
#         )
#         registry.register_tool_class(DateTimeTool)
#         registry.register_tool_class(JsonTool)
#         registry.register_tool_class(BaiduSearchTool)
#         registry.register_tool_class(MinerUTool)
#         registry.register_tool_class(TextInTool)
#
#         # 获取活跃的工具
#         import uuid
#         tools = registry.list_tools(workspace_id=uuid.UUID(workspace_id))
#         active_tools = [tool for tool in tools if tool.status.value == "active"]
#
#         # 转换为Langchain工具
#         langchain_tools = []
#         for tool_info in active_tools:
#             try:
#                 tool_instance = registry.get_tool(tool_info.id)
#                 if tool_instance:
#                     langchain_tool = LangchainAdapter.convert_tool(tool_instance)
#                     langchain_tools.append(langchain_tool)
#             except Exception as e:
#                 logger.error(f"转换工具失败: {tool_info.name}, 错误: {e}")
#
#         logger.info(f"为工作流获取了 {len(langchain_tools)} 个工具")
#         return langchain_tools
#
#     except Exception as e:
#         logger.error(f"获取工作流工具失败: {e}")
#         return []
#
#
# class ToolWorkflowNode:
#     """工具工作流节点 - 在工作流中执行工具"""
#
#     def __init__(self, node_config: dict, workflow_config: dict):
#         """初始化工具节点
#
#         Args:
#             node_config: 节点配置
#             workflow_config: 工作流配置
#         """
#         self.node_config = node_config
#         self.workflow_config = workflow_config
#         self.tool_id = node_config.get("tool_id")
#         self.tool_parameters = node_config.get("parameters", {})
#
#     async def run(self, state: WorkflowState) -> WorkflowState:
#         """执行工具节点"""
#         if not TOOL_MANAGEMENT_AVAILABLE:
#             logger.error("工具管理系统不可用")
#             state["error"] = "工具管理系统不可用"
#             return state
#
#         try:
#             from sqlalchemy.orm import Session
#             db = next(get_db())
#
#             # 创建工具执行器
#             registry = ToolRegistry(db)
#             executor = ToolExecutor(db, registry)
#
#             # 准备参数（支持变量替换）
#             parameters = self._prepare_parameters(state)
#
#             # 执行工具
#             result = await executor.execute_tool(
#                 tool_id=self.tool_id,
#                 parameters=parameters,
#                 user_id=uuid.UUID(state["user_id"]),
#                 workspace_id=uuid.UUID(state["workspace_id"])
#             )
#
#             # 更新状态
#             node_id = self.node_config.get("id")
#             if result.success:
#                 state["node_outputs"][node_id] = {
#                     "type": "tool",
#                     "tool_id": self.tool_id,
#                     "output": result.data,
#                     "execution_time": result.execution_time,
#                     "token_usage": result.token_usage
#                 }
#
#                 # 更新运行时变量
#                 if isinstance(result.data, dict):
#                     for key, value in result.data.items():
#                         state["runtime_vars"][f"{node_id}.{key}"] = value
#                 else:
#                     state["runtime_vars"][f"{node_id}.result"] = result.data
#             else:
#                 state["error"] = result.error
#                 state["error_node"] = node_id
#                 state["node_outputs"][node_id] = {
#                     "type": "tool",
#                     "tool_id": self.tool_id,
#                     "error": result.error,
#                     "execution_time": result.execution_time
#                 }
#
#             return state
#
#         except Exception as e:
#             logger.error(f"工具节点执行失败: {e}")
#             state["error"] = str(e)
#             state["error_node"] = self.node_config.get("id")
#             return state
#
#     def _prepare_parameters(self, state: WorkflowState) -> dict:
#         """准备工具参数（支持变量替换）"""
#         parameters = {}
#
#         for key, value in self.tool_parameters.items():
#             if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
#                 # 变量替换
#                 var_path = value[2:-1]
#
#                 # 支持多层级变量访问，如 ${sys.message} 或 ${node1.result}
#                 if "." in var_path:
#                     parts = var_path.split(".")
#                     current = state.get("variables", {})
#
#                     for part in parts:
#                         if isinstance(current, dict) and part in current:
#                             current = current[part]
#                         else:
#                             # 尝试从运行时变量获取
#                             runtime_key = ".".join(parts)
#                             current = state.get("runtime_vars", {}).get(runtime_key, value)
#                             break
#
#                     parameters[key] = current
#                 else:
#                     # 简单变量
#                     variables = state.get("variables", {})
#                     parameters[key] = variables.get(var_path, value)
#             else:
#                 parameters[key] = value
#
#         return parameters
#
#
# # 注册工具节点到NodeFactory（如果存在）
# try:
#     from app.core.workflow.nodes import NodeFactory
#     if hasattr(NodeFactory, 'register_node_type'):
#         NodeFactory.register_node_type("tool", ToolWorkflowNode)
#     logger.info("工具节点已注册到工作流系统")
# except Exception as e:
#     logger.warning(f"注册工具节点失败: {e}")
