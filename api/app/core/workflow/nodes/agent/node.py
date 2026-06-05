"""
Agent 节点实现

具备自主推理 + 工具调用能力（ReAct / Function Calling）的工作流节点。

内部复用 ``LangChainAgent`` 执行引擎驱动工具调用循环，复用 ``ToolService`` +
``LangchainToolWrapper`` 的工具适配层。配置与执行流程对齐 LLM 节点
（error_handle / memory / thinking）。
"""

import logging
import json
from copy import deepcopy
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from app.core.agent.langchain_agent import LangChainAgent
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.agent.config import AgentNodeConfig
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.enums import HttpErrorHandle
from app.core.workflow.nodes.llm.config import strip_unsupported_llm_params, validate_llm_param_constraints
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_db_read
from app.models import ModelCapability, ModelType
from app.schemas.model_schema import ModelInfo
from app.services.model_service import ModelConfigService
from app.services.tool_service import ToolService

logger = logging.getLogger(__name__)


class AgentNode(BaseNode):
    """Agent 节点

    支持流式和非流式输出。Agent 内部自主循环思考、选择并调用工具，直到得出最终答案。

    配置示例:
    {
        "type": "agent",
        "config": {
            "model_id": "uuid",
            "system_prompt": "你是一个专业的研究助手。",
            "message": "{{ sys.message }}",
            "tools": [
                {"tool_id": "uuid", "tool_type": "builtin", "operation": null, "enabled": true}
            ],
            "max_iterations": 10
        }
    }
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: AgentNodeConfig | None = None
        self.model_info: ModelInfo | None = None
        self._rendered_message: str = ""
        self._rendered_context: str = ""
        self._param_warnings: list[str] = []

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "output": VariableType.STRING,
            "branch_signal": VariableType.STRING,
            "reasoning_content": VariableType.STRING,
            "token_usage": VariableType.OBJECT,
            "files": VariableType.ARRAY_FILE,
            "json": VariableType.ARRAY_OBJECT,
            "param_warnings": VariableType.ARRAY_STRING,
        }

    # ------------------------------------------------------------------
    # 准备阶段
    # ------------------------------------------------------------------
    def _resolve_tenant_id(self, variable_pool: VariablePool) -> Any:
        """解析租户 ID（工具加载需要）"""
        tenant_id = self.get_variable("sys.tenant_id", variable_pool, strict=False)
        if tenant_id:
            return tenant_id

        workspace_id = self.get_variable("sys.workspace_id", variable_pool, strict=False)
        if workspace_id:
            from app.repositories.tool_repository import ToolRepository
            with get_db_read() as db:
                tenant_id = ToolRepository.get_tenant_id_by_workspace_id(db, workspace_id)
        return tenant_id

    def _load_tools(self, variable_pool: VariablePool) -> list:
        """根据配置加载工具并转换为 LangChain BaseTool 列表"""
        selectors = [s for s in (self.typed_config.tools or []) if s.enabled]
        if not selectors:
            return []

        tenant_id = self._resolve_tenant_id(variable_pool)
        if not tenant_id:
            logger.warning(f"节点 {self.node_id}: 缺少租户 ID，无法加载工具，Agent 将仅使用推理能力")
            return []

        user_id = self.get_variable("sys.user_id", variable_pool, strict=False)
        workspace_id = self.get_variable("sys.workspace_id", variable_pool, strict=False)

        langchain_tools = []
        with get_db_read() as db:
            tool_service = ToolService(db)
            for selector in selectors:
                try:
                    tool_instance = tool_service.get_tool_instance(selector.tool_id, tenant_id)
                    if not tool_instance:
                        logger.warning(f"节点 {self.node_id}: 工具 {selector.tool_id} 不存在或未激活，已跳过")
                        continue
                    tool_instance.set_runtime_context(user_id=user_id, workspace_id=workspace_id)
                    langchain_tools.append(
                        tool_instance.to_langchain_tool(selector.operation)
                    )
                except Exception as e:
                    logger.error(f"节点 {self.node_id}: 加载工具 {selector.tool_id} 失败: {e}")

        logger.debug(f"节点 {self.node_id} 加载了 {len(langchain_tools)} 个工具")
        return langchain_tools

    def _build_history(self, state: WorkflowState) -> list[dict[str, str]]:
        """构建历史消息（启用 memory 时）"""
        if not self.typed_config.memory.enable:
            return []

        state_messages = state.get("messages", [])
        if self.typed_config.memory.enable_window:
            window_size = self.typed_config.memory.window_size or 20
            state_messages = state_messages[-window_size:]
        history_messages = deepcopy(state_messages)
        history = []
        for message in history_messages:
            role = message.get("role")
            content = message.get("content")
            # Agent 历史仅支持纯文本，跳过多模态内容块
            if role in ("user", "assistant") and isinstance(content, str):
                history.append({"role": role, "content": content})
        return history

    @staticmethod
    def _selector_to_literal(selector: Any) -> str | None:
        if isinstance(selector, str):
            selector = selector.strip()
            if not selector:
                return None
            if selector.startswith("{{") and selector.endswith("}}"):
                return selector
            return "{{" + selector + "}}"
        return None

    @staticmethod
    def _context_value_to_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    def _resolve_context(self, variable_pool: VariablePool) -> str:
        context = self.typed_config.context if self.typed_config else ""
        if not context:
            return ""

        selector = self._selector_to_literal(context)
        if not selector:
            return ""

        value = variable_pool.get_value(selector, default=None, strict=False)
        return self._context_value_to_text(value)

    @staticmethod
    def _inject_context(message: str, context: str) -> str:
        if not context.strip():
            return message
        context_block = f"<context>\n{context}\n</context>"
        if "{{context}}" in message:
            return message.replace("{{context}}", context_block)
        return f"{message}\n\n{context_block}" if message else context_block

    async def _prepare_agent(
            self,
            state: WorkflowState,
            variable_pool: VariablePool,
            stream: bool = False
    ) -> tuple[LangChainAgent, str, list[dict[str, str]], str]:
        """解析配置，创建 LangChainAgent 实例

        Returns:
            (agent, message, history, strategy)
        """
        self.typed_config = AgentNodeConfig(**self.config)
        self._param_warnings = []

        model_config = self.typed_config.model
        params = model_config.completion_params
        model_id = model_config.model_id
        if not model_id:
            raise ValueError(f"节点 {self.node_id} 缺少 model_id 配置，请先选择模型")

        # 1. 渲染模板
        context = self._resolve_context(variable_pool)
        system_prompt = self._render_template(
            self.typed_config.system_prompt or "", variable_pool, strict=False
        )
        message_template = self.typed_config.message or ""
        if context.strip() and "{{context}}" in message_template:
            message_template = self._inject_context(message_template, context)
            message = self._render_template(message_template, variable_pool, strict=False)
        else:
            message = self._render_template(message_template, variable_pool, strict=False)
            message = self._inject_context(message, context)
        self._rendered_message = message
        self._rendered_context = context

        # 2. 获取模型配置
        with get_db_read() as db:
            config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
            if not config:
                raise BusinessException("配置的模型不存在", BizCode.NOT_FOUND)
            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)

            api_config = self.model_balance(config)
            model_info = ModelInfo(
                model_name=api_config.model_name,
                model_type=ModelType(config.type),
                api_key=api_config.api_key,
                api_base=api_config.api_base,
                provider=api_config.provider,
                is_omni=api_config.is_omni,
                capability=api_config.capability
            )
            self.model_info = model_info

        param_warnings = validate_llm_param_constraints(
            config=params,
            capability=model_info.capability or [],
            provider=model_info.provider or "",
            is_omni=model_info.is_omni,
        )
        if param_warnings:
            for warning in param_warnings:
                logger.warning(
                    f"Node {self.node_id} model parameter warning: {warning} "
                    f"(model={model_info.model_name}, provider={model_info.provider})"
                )
            self._param_warnings.extend(param_warnings)

        # 3. 加载工具
        langchain_tools = self._load_tools(variable_pool)

        # 4. 构建历史
        history = self._build_history(state)

        # 5. 思考模式参数
        deep_thinking = params.thinking.enable
        thinking_budget_tokens = params.thinking.budget.value if (
            params.thinking.budget.enable and params.thinking.budget.value is not None
        ) else None

        # 6. 创建 LangChainAgent
        extra_params: dict[str, Any] = {}
        if params.top_p.enable and params.top_p.value is not None:
            extra_params["top_p"] = params.top_p.value
        if params.top_k.enable and params.top_k.value is not None:
            extra_params["top_k"] = params.top_k.value
        if params.seed.enable and params.seed.value is not None:
            extra_params["seed"] = params.seed.value
        if params.repetition_penalty.enable and params.repetition_penalty.value is not None:
            extra_params["repetition_penalty"] = params.repetition_penalty.value
        if params.frequency_penalty.enable and params.frequency_penalty.value is not None:
            extra_params["frequency_penalty"] = params.frequency_penalty.value
        if params.presence_penalty.enable and params.presence_penalty.value is not None:
            extra_params["presence_penalty"] = params.presence_penalty.value
        if params.stop.enable and params.stop.value:
            extra_params["stop"] = params.stop.value[:4]
        if params.search:
            extra_params["enable_search"] = True

        json_output = params.json_output or (
            params.response_format.enable
            and params.response_format.value == "json_object"
        )
        if params.response_format.enable and params.response_format.value == "text":
            json_output = False

        capability_set = set(model_info.capability or [])
        if json_output and ModelCapability.JSON_OUTPUT not in capability_set:
            json_output = False

        if params.extra_headers.enable and params.extra_headers.value:
            try:
                extra_headers_dict = json.loads(params.extra_headers.value)
                extra_params["default_headers"] = extra_headers_dict
            except json.JSONDecodeError as e:
                logger.warning(f"Node {self.node_id}: extra_headers JSON parse failed: {e}")

        extra_params, strip_warnings = strip_unsupported_llm_params(
            extra_params, model_info.provider or "", model_info.is_omni
        )
        if strip_warnings:
            for warning in strip_warnings:
                logger.warning(
                    f"Node {self.node_id} stripped unsupported model parameter: {warning} "
                    f"(model={model_info.model_name}, provider={model_info.provider})"
                )
            self._param_warnings.extend(strip_warnings)

        strategy = self.typed_config.strategy
        agent = LangChainAgent(
            model_name=model_info.model_name,
            api_key=model_info.api_key,
            provider=model_info.provider or "openai",
            api_base=model_info.api_base,
            is_omni=model_info.is_omni,
            temperature=params.temperature if params.temperature is not None else 0.7,
            max_tokens=params.max_tokens if params.max_tokens is not None else 2000,
            system_prompt=system_prompt or "你是一个专业的AI助手",
            tools=langchain_tools,
            streaming=stream,
            max_iterations=self.typed_config.max_iterations,
            strategy=strategy,
            deep_thinking=deep_thinking,
            thinking_budget_tokens=thinking_budget_tokens,
            json_output=json_output,
            top_p=extra_params.get("top_p"),
            top_k=extra_params.get("top_k"),
            seed=extra_params.get("seed"),
            repetition_penalty=extra_params.get("repetition_penalty"),
            frequency_penalty=extra_params.get("frequency_penalty"),
            presence_penalty=extra_params.get("presence_penalty"),
            enable_search=bool(extra_params.get("enable_search")),
            stop=extra_params.get("stop"),
            extra_headers=extra_params.get("default_headers"),
            capability=model_info.capability,
        )

        return agent, message, history, strategy

    # ------------------------------------------------------------------
    # 非流式执行
    # ------------------------------------------------------------------
    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """非流式执行"""
        self.typed_config = AgentNodeConfig(**self.config)

        try:
            agent, message, history, strategy = await self._prepare_agent(state, variable_pool, stream=False)

            logger.info(f"节点 {self.node_id} 开始执行 Agent（非流式，策略={strategy}）")

            result = await agent.chat(message=message, history=history)
            content = result.get("content", "")
            reasoning_content = result.get("reasoning_content", "") or ""
            usage = result.get("usage", {}) or {}
            agent_log = result.get("agent_log") or {}

            logger.info(f"节点 {self.node_id} Agent 执行完成，输出长度: {len(content)}")

            return {
                "llm_result": AIMessage(
                    content=content,
                    response_metadata={
                        "token_usage": usage,
                        "reasoning_content": reasoning_content or None,
                    }
                ),
                "branch_signal": "SUCCESS",
                "reasoning_content": reasoning_content,
                "agent_log": agent_log,
                "param_warnings": self._param_warnings or [],
            }

        except Exception as e:
            logger.error(f"节点 {self.node_id} Agent 执行失败: {e}")
            return self._handle_agent_error(e)

    # ------------------------------------------------------------------
    # 流式执行
    # ------------------------------------------------------------------
    async def execute_stream(self, state: WorkflowState, variable_pool: VariablePool):
        """流式执行"""
        self.typed_config = AgentNodeConfig(**self.config)

        try:
            agent, message, history, strategy = await self._prepare_agent(state, variable_pool, stream=True)

            logger.info(f"节点 {self.node_id} 开始执行 Agent（流式，策略={strategy}）")

            full_response = ""
            full_reasoning = ""
            total_tokens = 0
            agent_log = {}
            reasoning_done_sent = False

            async for event in agent.chat_stream(message=message, history=history):
                # 1. 文本内容块（str）
                if isinstance(event, str):
                    if event:
                        full_response += event
                        yield {"__final__": False, "chunk": event, "field": "output"}
                # 2. token 统计（int）
                elif isinstance(event, int):
                    total_tokens = event
                # 3. 结构化事件（dict）
                elif isinstance(event, dict):
                    etype = event.get("type")
                    if etype == "reasoning":
                        rc = event.get("content", "")
                        if rc:
                            full_reasoning += rc
                            yield {"__final__": False, "chunk": rc, "field": "reasoning_content"}
                    elif etype == "tool_start":
                        self._emit_tool_event("agent_tool_start", event)
                    elif etype == "tool_end":
                        self._emit_tool_event("agent_tool_end", event)
                    elif etype == "tool_error":
                        self._emit_tool_event("agent_tool_error", event)
                    elif etype == "agent_log":
                        self._emit_agent_log_event(event.get("data") or {})
                    elif etype == "agent_log_final":
                        agent_log = event.get("data") or {}
                    # node_executions 事件用于记录，工作流层不单独处理

            # 推理段结束信号（推进流式游标）
            if full_reasoning and not reasoning_done_sent:
                yield {"__final__": False, "chunk": "", "done": True, "field": "reasoning_content"}

            # 输出段结束信号
            yield {"__final__": False, "chunk": "", "done": True, "field": "output"}

            logger.info(f"节点 {self.node_id} Agent 流式执行完成，输出长度: {len(full_response)}")

            final_message = AIMessage(
                content=full_response,
                response_metadata={
                    "token_usage": {"total_tokens": total_tokens} if total_tokens else {},
                    "reasoning_content": full_reasoning or None,
                }
            )

            yield {
                "__final__": True,
                "result": {
                    "llm_result": final_message,
                    "branch_signal": "SUCCESS",
                    "reasoning_content": full_reasoning,
                    "agent_log": agent_log,
                    "param_warnings": self._param_warnings or [],
                }
            }

        except Exception as e:
            logger.error(f"节点 {self.node_id} Agent 流式执行失败: {e}")
            error_result = self._handle_agent_error(e)
            yield {"__final__": True, "result": error_result}

    def _emit_tool_event(self, event_type: str, event: dict) -> None:
        """通过 stream writer 推送工具调用中间状态事件到前端"""
        try:
            writer = get_stream_writer()
            writer({
                "type": event_type,
                "node_id": self.node_id,
                "step_id": event.get("step_id"),
                "tool_name": event.get("name"),
                "tool_input": event.get("input"),
                "tool_output": event.get("output"),
                "error": event.get("error"),
                "meta": event.get("meta"),
            })
        except Exception as e:
            logger.debug(f"节点 {self.node_id}: 推送工具事件 {event_type} 失败: {e}")

    def _emit_agent_log_event(self, agent_log: dict) -> None:
        """通过 stream writer 推送 Agent 执行过程快照到前端"""
        try:
            writer = get_stream_writer()
            writer({
                "type": "agent_log",
                "data": {
                    "node_id": self.node_id,
                    "agent_log": agent_log,
                }
            })
        except Exception as e:
            logger.debug(f"节点 {self.node_id}: 推送 Agent 执行日志失败: {e}")

    # ------------------------------------------------------------------
    # 异常处理
    # ------------------------------------------------------------------
    def _handle_agent_error(self, error: Exception | None) -> dict:
        """处理 Agent 执行异常（与 LLMNode._handle_llm_error 一致）"""
        if self.typed_config is None:
            raise error

        match self.typed_config.error_handle.method:
            case HttpErrorHandle.NONE:
                raise error
            case HttpErrorHandle.DEFAULT:
                logger.warning(f"节点 {self.node_id}: Agent 执行失败，返回默认输出")
                default_output = self.typed_config.error_handle.output or ""
                return {
                    "llm_result": AIMessage(content=default_output, response_metadata={}),
                    "branch_signal": "SUCCESS",
                    "reasoning_content": "",
                    "param_warnings": self._param_warnings or [],
                }
            case HttpErrorHandle.BRANCH:
                logger.warning(f"节点 {self.node_id}: Agent 执行失败，切换到异常处理分支")
                return {
                    "llm_result": None,
                    "branch_signal": "ERROR",
                    "reasoning_content": "",
                    "param_warnings": self._param_warnings or [],
                }
        raise error

    # ------------------------------------------------------------------
    # 输出 / 输入提取
    # ------------------------------------------------------------------
    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        model_config = self.config.get("model") if isinstance(self.config.get("model"), dict) else {}
        model_id = model_config.get("model_id") or self.config.get("model_id")
        return {
            "message": self._rendered_message,
            "context": self._rendered_context,
            "config": {
                "model_id": str(model_id) if model_id else None,
                "max_iterations": self.config.get("max_iterations"),
                "tools": [
                    t.get("tool_id") for t in (self.config.get("tools") or [])
                    if isinstance(t, dict)
                ],
            }
        }

    def _extract_extra_fields(self, business_result: Any) -> dict:
        llm_result = business_result.get("llm_result") if isinstance(business_result, dict) else business_result
        if isinstance(llm_result, AIMessage):
            process = {}
            extra = {"process": process}
            if isinstance(business_result, dict) and business_result.get("agent_log"):
                extra["agent_log"] = business_result.get("agent_log")
            return extra
        return {}

    def _extract_output(self, business_result: Any) -> dict:
        """从业务结果提取输出变量"""
        if isinstance(business_result, dict) and "branch_signal" in business_result:
            llm_result = business_result.get("llm_result")
            if isinstance(llm_result, AIMessage):
                output = llm_result.content
            else:
                output = str(llm_result) if llm_result else ""
            result = {
                "output": output,
                "branch_signal": business_result["branch_signal"],
                "reasoning_content": business_result.get("reasoning_content") or "",
                "token_usage": self._extract_token_usage(business_result) or {},
                "files": business_result.get("files") or [],
                "json": business_result.get("json") or [],
                "param_warnings": business_result.get("param_warnings") or [],
            }
            return result
        if isinstance(business_result, AIMessage):
            return {
                "output": business_result.content,
                "branch_signal": "SUCCESS",
                "reasoning_content": "",
                "token_usage": self._extract_token_usage(business_result) or {},
                "files": [],
                "json": [],
                "param_warnings": [],
            }
        return {
            "output": str(business_result),
            "branch_signal": "SUCCESS",
            "reasoning_content": "",
            "token_usage": {},
            "files": [],
            "json": [],
            "param_warnings": [],
        }

    def _extract_token_usage(self, business_result: Any) -> dict[str, int] | None:
        """从业务结果提取 token 使用情况"""
        llm_result = business_result
        if isinstance(business_result, dict):
            llm_result = business_result.get("llm_result", business_result)
        if isinstance(llm_result, AIMessage) and hasattr(llm_result, 'response_metadata'):
            usage = llm_result.response_metadata.get('token_usage') or {}
            if usage:
                return {
                    "prompt_tokens": usage.get('prompt_tokens', 0),
                    "completion_tokens": usage.get('completion_tokens', 0),
                    "total_tokens": usage.get('total_tokens', 0),
                }
        return None
