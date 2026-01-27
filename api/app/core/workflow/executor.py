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

from app.core.workflow.expression_evaluator import evaluate_expression
from app.core.workflow.graph_builder import GraphBuilder, StreamOutputConfig
from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_config import VariableType
from app.core.workflow.nodes.enums import NodeType
from app.core.workflow.template_renderer import render_template

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
        self.end_outputs: dict[str, StreamOutputConfig] = {}
        self.activate_end: str | None = None

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

    def _build_final_output(self, result, elapsed_time, final_output):
        node_outputs = result.get("node_outputs", {})
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

    def _update_end_activate(self, node_id):
        for node in self.end_outputs.keys():
            self.end_outputs[node].update_activate(node_id)
            if self.end_outputs[node].activate and self.activate_end is None:
                self.activate_end = node

    @staticmethod
    def _trans_output_string(content):
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            return "\n".join(content)
        else:
            return str(content)

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
        self.end_outputs = builder.end_node_map
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
            full_content = ''
            for end_info in self.end_outputs.values():
                output_template = "".join([output.literal for output in end_info.outputs])
                full_content += render_template(
                    output_template,
                    result.get("variables", {}),
                    result.get("runtime_vars", {}),
                    strict=False
                )
            result["messages"].extend(
                [
                    {
                        "role": "user",
                        "content": input_data.get("message", '')
                    },
                    {
                        "role": "assistant",
                        "content": full_content
                    }
                ]
            )
            # 计算耗时
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.info(f"工作流执行完成: execution_id={self.execution_id}, elapsed_time={elapsed_time:.2f}s")

            return self._build_final_output(result, elapsed_time, full_content)

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
                "timestamp": int(start_time.timestamp() * 1000)
            }
        }

        # 1. 构建图
        graph = self.build_graph(stream=True)

        # 2. 初始化状态（自动注入系统变量）
        initial_state = self._prepare_initial_state(input_data)
        # 3. Execute workflow
        try:
            chunk_count = 0
            full_content = ''

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
                    if event_type == "node_chunk":
                        node_id = data.get("node_id")
                        if self.activate_end:
                            end_info = self.end_outputs.get(self.activate_end)
                            if not end_info or end_info.cursor >= len(end_info.outputs):
                                continue
                            current_output = end_info.outputs[end_info.cursor]
                            if current_output.is_variable and current_output.depends_on_node(node_id):
                                if data.get("done"):
                                    end_info.cursor += 1
                                else:
                                    full_content += data.get("chunk")
                                    yield {
                                        "event": "message",
                                        "data": {
                                            "chunk": data.get("chunk")
                                        }
                                    }
                        logger.info(f"[CUSTOM] ✅ 收到 {event_type} #{chunk_count} from {data.get('node_id')}"
                                    f"- execution_id: {self.execution_id}")

                    elif event_type == "node_error":
                        yield {
                            "event": event_type,  # "message" or "node_chunk"
                            "data": {
                                "node_id": data.get("node_id"),
                                "status": "failed",
                                "input": data.get("input_data"),
                                "elapsed_time": data.get("elapsed_time"),
                                "output": None,
                                "error": data.get("error")
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
                                "timestamp": int(datetime.datetime.fromisoformat(
                                    data.get("timestamp")
                                ).timestamp() * 1000),
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
                                "timestamp": int(datetime.datetime.fromisoformat(
                                    data.get("timestamp")
                                ).timestamp() * 1000),
                                "input": result.get("node_outputs", {}).get(node_name, {}).get("input"),
                                "output": result.get("node_outputs", {}).get(node_name, {}).get("output"),
                                "elapsed_time": result.get("node_outputs", {}).get(node_name, {}).get("elapsed_time"),
                            }
                        }

                elif mode == "updates":
                    # Handle state updates - store final state
                    for node_id in data.keys():
                        self._update_end_activate(node_id)
                    wait = False
                    state = graph.get_state(config=self.checkpoint_config)
                    node_outputs = state.values.get("runtime_vars", {})
                    for _ in data.keys():
                        node_outputs = node_outputs | data.get(_).get("runtime_vars", {})

                    while self.activate_end and not wait:
                        message = ''
                        logger.info(self.activate_end)
                        end_info = self.end_outputs[self.activate_end]
                        content = end_info.outputs[end_info.cursor]
                        while content.activate:
                            if not content.is_variable:
                                full_content += content.literal
                                message += content.literal
                            else:
                                try:
                                    chunk = evaluate_expression(
                                        content.literal,
                                        variables={},
                                        node_outputs=node_outputs
                                    )
                                    chunk = self._trans_output_string(chunk)
                                    message += chunk
                                    full_content += chunk
                                except ValueError:
                                    pass
                            end_info.cursor += 1
                            if end_info.cursor == len(end_info.outputs):
                                break
                            content = end_info.outputs[end_info.cursor]
                        if end_info.cursor != len(end_info.outputs):
                            wait = True
                        else:
                            self.end_outputs.pop(self.activate_end)
                            self.activate_end = None
                            for node_id in data.keys():
                                self._update_end_activate(node_id)
                        if message:
                            yield {
                                "event": "message",
                                "data": {
                                    "chunk": message
                                }
                            }

                    logger.debug(f"[UPDATES] 收到 state 更新 from {list(data.keys())} "
                                 f"- execution_id: {self.execution_id}")

            result = graph.get_state(self.checkpoint_config).values
            while self.activate_end:
                message = ''
                end_info = self.end_outputs[self.activate_end]
                content = end_info.outputs[end_info.cursor]
                if not content.is_variable:
                    message += content.literal
                else:
                    node_outputs = result.get("runtime_vars", {})
                    variables = result.get("variables", {})
                    try:
                        chunk = evaluate_expression(
                            content.literal,
                            variables=variables,
                            node_outputs=node_outputs
                        )
                        chunk = self._trans_output_string(chunk)
                        message += chunk
                        full_content += chunk
                    except ValueError:
                        pass
                end_info.cursor += 1
                if end_info.cursor == len(end_info.outputs):
                    self.end_outputs.pop(self.activate_end)
                    self.activate_end = None
                    if self.end_outputs:
                        self.activate_end = list(self.end_outputs.keys())[0]
                if message:
                    yield {
                        "event": "message",
                        "data": {
                            "chunk": message
                        }
                    }

            # 计算耗时
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()
            result = graph.get_state(self.checkpoint_config).values
            logger.info(result)
            result["messages"].extend(
                [
                    {
                        "role": "user",
                        "content": input_data.get("message", '')
                    },
                    {
                        "role": "assistant",
                        "content": full_content
                    }
                ]
            )
            logger.info(
                f"Workflow execution completed (streaming), "
                f"total chunks: {chunk_count}, elapsed: {elapsed_time:.2f}s, execution_id: {self.execution_id}"
            )

            # 发送 workflow_end 事件
            yield {
                "event": "workflow_end",
                "data": self._build_final_output(result, elapsed_time, full_content)
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
