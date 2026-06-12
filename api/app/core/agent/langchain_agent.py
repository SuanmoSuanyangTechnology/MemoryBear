"""
LangChain Agent 封装

使用 LangChain 1.x 标准方式
- 使用 create_agent 创建 agent graph
- 支持工具调用循环
- 支持流式输出
- 使用 RedBearLLM 支持多提供商
"""

import json
import time
import uuid as _uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError

from app.core.logging_config import get_business_logger
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.utils.datetime_utils import to_iso_z, utcnow
from app.models.models_model import ModelType

logger = get_business_logger()


class AgentTraceRecorder:
    """Builds a Dify-style agent execution log grouped by LLM round."""

    def __init__(self, *, model: str, strategy: str, system_prompt: str, messages: Sequence[BaseMessage]):
        self._started_at = time.time()
        self._model = model
        self._current_iteration: dict[str, Any] | None = None
        self._current_llm: dict[str, Any] | None = None
        self._tools_by_id: dict[str, dict[str, Any]] = {}
        self.log: dict[str, Any] = {
            "meta": {
                "status": "running",
                "model": model,
                "strategy": strategy,
                "start_time": self._now_iso(),
                "elapsed_time": 0,
                "total_tokens": 0,
                "iterations": 0,
            },
            "iterations": [],
        }
        self._base_input = {
            "system_prompt": system_prompt,
            "messages": [self._message_to_dict(msg) for msg in messages],
        }

    @staticmethod
    def _now_iso() -> str:
        return to_iso_z(utcnow()) or ""

    @classmethod
    def _jsonable(cls, value: Any, max_length: int = 2000) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:max_length] + "...[truncated]" if len(value) > max_length else value
        if isinstance(value, BaseMessage):
            return cls._message_to_dict(value)
        if isinstance(value, dict):
            return {str(k): cls._jsonable(v, max_length) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._jsonable(item, max_length) for item in value]
        content = getattr(value, "content", None)
        if content is not None:
            return cls._jsonable(content, max_length)
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
        return text[:max_length] + "...[truncated]" if len(text) > max_length else text

    @classmethod
    def _message_to_dict(cls, message: BaseMessage) -> dict[str, Any]:
        return {
            "role": getattr(message, "type", message.__class__.__name__.replace("Message", "").lower()),
            "content": cls._jsonable(getattr(message, "content", "")),
        }

    @staticmethod
    def _strip_private(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: AgentTraceRecorder._strip_private(v) for k, v in value.items() if not str(k).startswith("_")}
        if isinstance(value, list):
            return [AgentTraceRecorder._strip_private(item) for item in value]
        return value

    def start_llm(self, raw_input: Any | None = None) -> dict[str, Any]:
        self.finish_llm()
        index = len(self.log["iterations"]) + 1
        llm_input = dict(self._base_input)
        if raw_input is not None:
            llm_input["raw"] = self._jsonable(raw_input)
        iteration = {
            "id": str(_uuid.uuid4()),
            "index": index,
            "created_at": self._now_iso(),
            "llm": {
                "id": str(_uuid.uuid4()),
                "model": self._model,
                "status": "running",
                "input": llm_input,
                "output": "",
                "reasoning_content": "",
                "tokens": 0,
                "elapsed_time": 0,
                "error": None,
                "_started_at": time.time(),
            },
            "tool_calls": [],
        }
        self.log["iterations"].append(iteration)
        self._current_iteration = iteration
        self._current_llm = iteration["llm"]
        self.log["meta"]["iterations"] = len(self.log["iterations"])
        return iteration

    def ensure_iteration(self) -> dict[str, Any]:
        if not self._current_iteration:
            return self.start_llm()
        return self._current_iteration

    def append_llm_chunk(self, *, output: str = "", reasoning: str = "") -> None:
        llm = self.ensure_iteration()["llm"]
        if output:
            llm["output"] = f"{llm.get('output', '')}{output}"
        if reasoning:
            llm["reasoning_content"] = f"{llm.get('reasoning_content', '')}{reasoning}"

    def finish_llm(self, *, output: str = "", tokens: int = 0, error: str | None = None) -> None:
        llm = self._current_llm
        if not llm or llm.get("status") != "running":
            return
        if output and not llm.get("output"):
            llm["output"] = output
        if tokens:
            llm["tokens"] = tokens
            self.log["meta"]["total_tokens"] = int(self.log["meta"].get("total_tokens") or 0) + tokens
        llm["status"] = "error" if error else "success"
        llm["error"] = error
        llm["elapsed_time"] = round(time.time() - llm.get("_started_at", time.time()), 3)

    def start_tool(self, *, step_id: str, tool_name: str, tool_input: Any, meta: dict[str, Any] | None) -> dict[str, Any]:
        iteration = self.ensure_iteration()
        tool_call = {
            "id": step_id,
            "status": "running",
            "tool_name": tool_name,
            "tool_label": (meta or {}).get("label") or (meta or {}).get("name") or tool_name,
            "tool_input": self._jsonable(tool_input),
            "tool_output": None,
            "tool_parameters": (meta or {}).get("tool_parameters", {}),
            "tool_icon": (meta or {}).get("icon") or (meta or {}).get("icon_url"),
            "time_cost": 0,
            "error": None,
            "meta": self._jsonable(meta or {}),
            "_started_at": time.time(),
        }
        iteration["tool_calls"].append(tool_call)
        self._tools_by_id[step_id] = tool_call
        return tool_call

    def finish_tool(
            self,
            *,
            step_id: str | None,
            tool_name: str,
            status: str,
            output: Any = None,
            error: str | None = None,
            meta: dict[str, Any] | None = None,
    ) -> None:
        tool_call = self._tools_by_id.get(step_id or "")
        if not tool_call:
            for iteration in reversed(self.log["iterations"]):
                for item in reversed(iteration.get("tool_calls") or []):
                    if item.get("tool_name") == tool_name and item.get("status") == "running":
                        tool_call = item
                        break
                if tool_call:
                    break
        if not tool_call:
            return
        tool_call["status"] = status
        tool_call["tool_output"] = self._jsonable(output)
        tool_call["error"] = error
        if meta is not None:
            tool_call["meta"] = self._jsonable(meta)
        tool_call["time_cost"] = round(time.time() - tool_call.get("_started_at", time.time()), 3)

    def finalize(self, *, status: str = "success", total_tokens: int = 0, error: str | None = None) -> dict[str, Any]:
        final_error = error or (None if status == "success" else status)
        self.finish_llm(tokens=0, error=final_error)
        self.log["meta"]["status"] = status
        self.log["meta"]["elapsed_time"] = round(time.time() - self._started_at, 3)
        if total_tokens:
            self.log["meta"]["total_tokens"] = total_tokens
        if final_error:
            self.log["meta"]["error"] = final_error
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        self.log["meta"]["elapsed_time"] = round(time.time() - self._started_at, 3)
        self.log["meta"]["iterations"] = len(self.log["iterations"])
        return self._strip_private(self.log)


class LangChainAgent:

    def __init__(
            self,
            model_name: str,
            api_key: str,
            provider: str = "openai",
            api_base: Optional[str] = None,
            is_omni: bool = False,
            temperature: float = 0.7,
            max_tokens: int = 2000,
            system_prompt: Optional[str] = None,
            tools: Optional[Sequence[BaseTool]] = None,
            streaming: bool = False,
            top_p: Optional[float] = None,
            top_k: Optional[int] = None,
            seed: Optional[int] = None,
            repetition_penalty: Optional[float] = None,
            frequency_penalty: Optional[float] = None,
            presence_penalty: Optional[float] = None,
            enable_search: bool = False,
            stop: Optional[List[str]] = None,
            extra_headers: Optional[Dict[str, Any]] = None,
            max_iterations: Optional[int] = None,  # 最大迭代次数（None 表示自动计算）
            strategy: str = "react",  # Agent 执行策略：'react' 或 'function_calling'
            deep_thinking: bool = False,  # 是否启用深度思考模式
            thinking_budget_tokens: Optional[int] = None,  # 深度思考 token 预算
            json_output: bool = False,  # 是否强制 JSON 输出
            capability: Optional[List[str]] = None  # 模型能力列表，用于校验是否支持深度思考
    ):
        """初始化 LangChain Agent

        Args:
            model_name: 模型名称
            api_key: API Key
            provider: 提供商（openai, xinference, gpustack, ollama, dashscope）
            api_base: API 基础 URL
            temperature: 温度参数
            max_tokens: 最大 token 数
            system_prompt: 系统提示词
            tools: 工具列表（可选，框架自动走 ReAct 循环）
            streaming: 是否启用流式输出
            max_iterations: 最大迭代次数（None 表示自动计算：基础 5 次 + 每个工具 2 次）
            strategy: Agent 执行策略（'react' 使用 ReAct 推理循环，'function_calling' 使用原生工具调用）
        """
        self.model_name = model_name
        self.provider = provider
        self.tools = tools or []
        self.streaming = streaming
        self.is_omni = is_omni
        self.strategy = strategy

        # 构建工具名 → 元数据映射（用于执行记录中补充具体资源信息）
        self._tool_meta_map: Dict[str, Dict[str, Any]] = {}
        for t in self.tools:
            tool_name = getattr(t, "name", None)
            tool_meta = getattr(t, "_tool_meta", None)
            if tool_name and tool_meta:
                self._tool_meta_map[tool_name] = tool_meta

        # 根据工具数量动态调整最大迭代次数
        # 基础值 + 每个工具额外的调用机会
        if max_iterations is None:
            # 自动计算：基础 5 次 + 每个工具 2 次额外机会
            self.max_iterations = 5 + len(self.tools) * 2
        else:
            self.max_iterations = max_iterations

        self.system_prompt = system_prompt or "你是一个专业的AI助手"

        # 所有 provider 统一注入 JSON prompt 兜底，确保即使 API 层 response_format 未生效也能引导 JSON 输出
        if json_output:
            self.system_prompt += "\n请仅输出一个合法JSON对象，不要输出Markdown代码块或额外说明。"

        logger.debug(
            f"Agent 迭代次数配置: max_iterations={self.max_iterations}, "
            f"tool_count={len(self.tools)}, "
            f"auto_calculated={max_iterations is None}"
        )

        # 创建 RedBearLLM，capability 校验由 RedBearModelConfig 统一处理
        extra_params: Dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "streaming": streaming,
        }
        optional_params = {
            "top_p": top_p,
            "top_k": top_k,
            "seed": seed,
            "repetition_penalty": repetition_penalty,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
        }
        for key, value in optional_params.items():
            if value is not None:
                extra_params[key] = value
        if enable_search:
            extra_params["enable_search"] = True
        if stop:
            extra_params["stop"] = stop[:4]
        if extra_headers:
            extra_params["default_headers"] = extra_headers

        model_config = RedBearModelConfig(
            model_name=model_name,
            provider=provider,
            api_key=api_key,
            base_url=api_base,
            is_omni=is_omni,
            capability=capability,
            deep_thinking=deep_thinking,
            thinking_budget_tokens=thinking_budget_tokens,
            json_output=json_output,
            extra_params=extra_params
        )

        self.llm = RedBearLLM(model_config, type=ModelType.CHAT)
        # 从经过校验的 config 读取实际生效的能力开关
        self.deep_thinking = model_config.deep_thinking
        self.json_output = model_config.json_output

        # 获取底层模型用于真正的流式调用
        self._underlying_llm = self.llm._model if hasattr(self.llm, '_model') else self.llm

        # 确保底层模型也启用流式
        if streaming and hasattr(self._underlying_llm, 'streaming'):
            self._underlying_llm.streaming = True

        # 使用 create_agent 创建 agent graph（LangChain 1.x 标准方式）
        # 无论是否有工具，都使用 agent 统一处理
        self.agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self.system_prompt
        )

        logger.info(
            "LangChain Agent 初始化完成",
            extra={
                "model": model_name,
                "provider": provider,
                "has_api_base": bool(api_base),
                "temperature": temperature,
                "streaming": streaming,
                "max_iterations": self.max_iterations,
                "strategy": self.strategy,
                "tool_count": len(self.tools),
                "tool_names": [tool.name for tool in self.tools] if self.tools else [],
            }
        )

    def _prepare_messages(
            self,
            message: str,
            history: Optional[List[Dict[str, str]]] = None,
            context: Optional[str] = None,
            files: Optional[List[Dict[str, Any]]] = None
    ) -> List[BaseMessage]:
        """准备消息列表

        Args:
            message: 用户消息
            history: 历史消息列表
            context: 上下文信息
            files: 多模态文件内容列表（已处理）

        Returns:
            List[BaseMessage]: 消息列表
        """
        messages: list = []

        # 添加历史消息
        if history:
            for msg in history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

        # 添加当前用户消息
        user_content = message
        if context:
            user_content = f"参考信息：\n{context}\n\n用户问题：\n{user_content}"

        # 构建用户消息（支持多模态）
        if files and len(files) > 0:
            content_parts = self._build_multimodal_content(user_content, files)
            messages.append(HumanMessage(content=content_parts))
        else:
            # 纯文本消息
            messages.append(HumanMessage(content=user_content))

        return messages

    @staticmethod
    def _extract_tokens_from_message(msg) -> int:
        """从 AIMessage 或类似对象中提取 total_tokens，兼容多种 provider 格式

        支持的格式：
        - response_metadata.token_usage.total_tokens (OpenAI/ChatOpenAI)
        - response_metadata.usage.total_tokens (部分 provider)
        - usage_metadata.total_tokens (LangChain 新版)
        """
        total = 0
        # 1. response_metadata
        response_meta = getattr(msg, "response_metadata", None)
        if response_meta and isinstance(response_meta, dict):
            # 尝试 token_usage 路径
            token_usage = response_meta.get("token_usage") or response_meta.get("usage", {})
            if isinstance(token_usage, dict):
                total = token_usage.get("total_tokens", 0)
        # 2. usage_metadata（LangChain 新版 AIMessage 属性）
        if not total:
            usage_meta = getattr(msg, "usage_metadata", None)
            if usage_meta:
                if isinstance(usage_meta, dict):
                    total = usage_meta.get("total_tokens", 0)
                else:
                    total = getattr(usage_meta, "total_tokens", 0)
        return total or 0

    def _build_multimodal_content(self, text: str, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        构建多模态消息内容
        
        Args:
            text: 文本内容
            files: 文件列表（已由 MultimodalService 处理为对应 provider 的格式）
            
        Returns:
            List[Dict]: 消息内容列表
        """
        # 根据 provider 使用不同的文本格式
        # if (self.provider.lower() in [ModelProvider.BEDROCK, ModelProvider.OPENAI, ModelProvider.XINFERENCE,
        #                               ModelProvider.GPUSTACK] or (
        #         self.provider.lower() == ModelProvider.DASHSCOPE and self.is_omni)):
        #     # Anthropic/Bedrock/Xinference/Gpustack/Openai: {"type": "text", "text": "..."}
        #     content_parts = [{"type": "text", "text": text}]
        # else:
        #     # 通义千问等: {"text": "..."}
        #     content_parts = [{"type": "text", "text": text}]
        content_parts = [{"type": "text", "text": text}]

        # 添加文件内容
        # MultimodalService 已经根据 provider 返回了正确格式，直接使用
        content_parts.extend(files)

        logger.debug(
            f"构建多模态消息: provider={self.provider}, "
            f"parts={len(content_parts)}, "
            f"files={len(files)}"
        )

        return content_parts

    @staticmethod
    def _extract_reasoning_content(msg) -> str:
        """从 AIMessage 中提取深度思考内容（reasoning_content）

        所有 provider 统一通过 additional_kwargs.reasoning_content 传递：
        - DeepSeek-R1 / QwQ: 原生字段
        - Volcano (Doubao-thinking): 由 VolcanoChatOpenAI 从 delta.reasoning_content 注入
        """
        additional = getattr(msg, "additional_kwargs", None) or {}
        return additional.get("reasoning_content") or additional.get("reasoning", "")

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        text_parts.append(str(item.get("text") or ""))
                    elif item.get("type") == "text":
                        text_parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            return "".join(text_parts)
        return str(content)

    @staticmethod
    def _extract_message_from_event_output(output: Any) -> AIMessage | None:
        if isinstance(output, AIMessage):
            return output
        if isinstance(output, dict):
            value = output.get("message") or output.get("output")
            if isinstance(value, AIMessage):
                return value
            generations = output.get("generations")
            if generations and isinstance(generations, list):
                for generation_group in generations:
                    items = generation_group if isinstance(generation_group, list) else [generation_group]
                    for generation in items:
                        message = getattr(generation, "message", None)
                        if isinstance(message, AIMessage):
                            return message
        message = getattr(output, "message", None)
        if isinstance(message, AIMessage):
            return message
        return None

    @staticmethod
    def _truncate_data(data, max_length: int = 2000) -> Any:
        if data is None:
            return None
        if isinstance(data, str):
            return data[:max_length] + "..." if len(data) > max_length else data
        try:
            text = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(data)
        return text[:max_length] + "...[truncated]" if len(text) > max_length else text

    def _extract_tool_sources(self, tool_name: str, node: Dict[str, Any]) -> None:
        """从工具实例中提取来源信息，合并到 node["meta"] 中"""
        for t in self.tools:
            t_name = getattr(t, "name", None)
            if t_name == tool_name:
                sources = getattr(t, "_last_sources", None)
                if sources:
                    if not node.get("meta"):
                        node["meta"] = {}
                    node["meta"]["sources"] = sources
                    t._last_sources = []
                break

    async def chat(
            self,
            message: str,
            history: Optional[List[Dict[str, str]]] = None,
            context: Optional[str] = None,
            files: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """执行对话

        Args:
            message: 用户消息
            history: 历史消息列表 [{"role": "user/assistant", "content": "..."}]
            context: 上下文信息（如知识库检索结果）
            files: 多模态文件

        Returns:
            Dict: 包含 content、node_executions 和元数据的字典
        """
        start_time = time.time()
        # 收集节点执行记录
        node_executions: List[Dict[str, Any]] = []
        # 用于匹配 tool_start/tool_end 的临时栈
        _tool_stack: List[Dict[str, Any]] = []

        try:
            # 准备消息列表（支持多模态）
            messages = self._prepare_messages(message, history, context, files)
            trace = AgentTraceRecorder(
                model=self.model_name,
                strategy=self.strategy,
                system_prompt=self.system_prompt,
                messages=messages,
            )

            logger.debug(
                "准备调用 LangChain Agent",
                extra={
                    "has_context": bool(context),
                    "has_history": bool(history),
                    "has_tools": bool(self.tools),
                    "has_files": bool(files),
                    "message_count": len(messages),
                    "max_iterations": self.max_iterations
                }
            )

            # 使用 astream_events 捕获中间步骤
            content = ""
            total_tokens = 0
            reasoning_content = ""
            last_event = {}

            try:
                async for event in self.agent.astream_events(
                    {"messages": messages},
                    version="v2",
                    config={"recursion_limit": self.max_iterations}
                ):
                    last_event = event
                    kind = event.get("event")

                    if kind in ("on_chat_model_start", "on_llm_start"):
                        trace.start_llm(event.get("data", {}).get("input"))

                    elif kind in ("on_chat_model_end", "on_llm_end"):
                        output_message = self._extract_message_from_event_output(event.get("data", {}).get("output"))
                        output_text = self._content_to_text(output_message.content) if output_message else ""
                        tokens = self._extract_tokens_from_message(output_message) if output_message else 0
                        trace.finish_llm(output=output_text, tokens=tokens)

                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        tool_input = event.get("data", {}).get("input")
                        step_id = str(_uuid.uuid4())
                        tool_meta = self._tool_meta_map.get(tool_name, {})
                        node = {
                            "step_id": step_id,
                            "node_type": "tool",
                            "node_name": tool_name,
                            "status": "running",
                            "input": self._truncate_data(tool_input),
                            "output": None,
                            "elapsed_time": 0,
                            "started_at": time.time(),
                            "error": None,
                            "meta": tool_meta if tool_meta else None,
                        }
                        _tool_stack.append(node)
                        node_executions.append(node)
                        trace.start_tool(
                            step_id=step_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            meta=tool_meta if tool_meta else None,
                        )

                    elif kind == "on_tool_end":
                        tool_output = event.get("data", {}).get("output")
                        tool_name = event.get("name", "unknown")
                        matched_node = None
                        if _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        if matched_node:
                            matched_node["status"] = "completed"
                            matched_node["output"] = self._truncate_data(tool_output)
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )
                            # 提取工具的额外来源信息（如知识库检索来源）
                            self._extract_tool_sources(tool_name, matched_node)

                        trace.finish_tool(
                            step_id=matched_node.get("step_id") if matched_node else None,
                            tool_name=tool_name,
                            status="success",
                            output=tool_output,
                            meta=matched_node.get("meta") if matched_node else None,
                        )

                    elif kind == "on_tool_error":
                        error_msg = str(event.get("data", {}).get("error", ""))
                        tool_name = event.get("name", "unknown")
                        matched_node = None
                        if _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        if matched_node:
                            matched_node["status"] = "failed"
                            matched_node["error"] = error_msg[:500]
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )

                        trace.finish_tool(
                            step_id=matched_node.get("step_id") if matched_node else None,
                            tool_name=tool_name,
                            status="error",
                            error=error_msg[:500],
                            meta=matched_node.get("meta") if matched_node else None,
                        )

                    elif kind == "on_chat_model_stream":
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content"):
                            if self.deep_thinking:
                                rc = self._extract_reasoning_content(chunk)
                                if rc:
                                    reasoning_content += rc
                                    trace.append_llm_chunk(reasoning=rc)
                            chunk_content = chunk.content
                            chunk_text = self._content_to_text(chunk_content)
                            if chunk_text:
                                content += chunk_text
                                trace.append_llm_chunk(output=chunk_text)

                # 从最后一个事件中提取 token 统计
                output_messages = last_event.get("data", {}).get("output", {}).get("messages", [])
                for msg in reversed(output_messages):
                    if isinstance(msg, AIMessage):
                        total_tokens = self._extract_tokens_from_message(msg)
                        if not content:
                            # 如果流式没有拼出内容，从最终消息中提取
                            if isinstance(msg.content, str):
                                content = msg.content
                            elif isinstance(msg.content, list):
                                text_parts = []
                                for item in msg.content:
                                    if isinstance(item, dict):
                                        if "text" in item:
                                            text_parts.append(item.get("text", ""))
                                        elif item.get("type") == "text":
                                            text_parts.append(item.get("text", ""))
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                                content = "".join(text_parts)
                            else:
                                content = str(msg.content)
                        if self.deep_thinking and not reasoning_content:
                            reasoning_content = self._extract_reasoning_content(msg)
                        break

            except (RecursionError, GraphRecursionError) as e:
                logger.warning(
                    f"Agent 达到最大迭代次数限制 ({self.max_iterations})，可能存在工具调用循环",
                    extra={"error": str(e)}
                )
                # 标记未完成的工具为失败
                for node in _tool_stack:
                    node["status"] = "failed"
                    node["error"] = "达到最大迭代次数限制"
                    node["elapsed_time"] = round(
                        (time.time() - node.get("started_at", time.time())) * 1000, 2
                    )
                _tool_stack.clear()

                # 清理 started_at（不需要持久化）
                for node in node_executions:
                    node.pop("started_at", None)

                return {
                    "content": f"抱歉，我在处理您的请求时遇到了问题。已达到最大处理步骤限制（{self.max_iterations}次）。请尝试简化您的问题或稍后再试。",
                    "model": self.model_name,
                    "elapsed_time": time.time() - start_time,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0
                    },
                    "node_executions": node_executions,
                    "agent_log": trace.finalize(
                        status="error",
                        error=f"Reached max iteration limit: {self.max_iterations}",
                    ),
                }

            trace.finish_llm(output=content, tokens=total_tokens)
            logger.info(f"最终提取的内容长度: {len(content)}")

            # 清理 started_at 并确保所有节点都有最终状态
            for node in node_executions:
                node.pop("started_at", None)
                # 如果执行到这里还是 running，说明 on_tool_end 事件丢失，标记为 completed
                if node.get("status") == "running":
                    node["status"] = "completed"

            elapsed_time = time.time() - start_time
            response = {
                "content": content,
                "model": self.model_name,
                "elapsed_time": elapsed_time,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": total_tokens
                },
                "node_executions": node_executions,
                "agent_log": trace.finalize(status="success", total_tokens=total_tokens),
            }
            if reasoning_content:
                response["reasoning_content"] = reasoning_content

            logger.debug(
                "Agent 调用完成",
                extra={
                    "elapsed_time": elapsed_time,
                    "content_length": len(response["content"]),
                    "node_execution_count": len(node_executions)
                }
            )

            return response

        except Exception as e:
            logger.error("Agent 调用失败", extra={"error": str(e)})
            raise

    async def chat_stream(
            self,
            message: str,
            history: Optional[List[Dict[str, str]]] = None,
            context: Optional[str] = None,
            files: Optional[List[Dict[str, Any]]] = None
    ) -> AsyncGenerator[str | int | dict[str, Any] | list, None]:
        """执行流式对话

        Args:
            message: 用户消息
            history: 历史消息列表
            context: 上下文信息
            files: 多模态文件

        Yields:
            str: 消息内容块
            int: token 统计
            Dict: 深度思考内容 {"type": "reasoning", "content": "..."}
            list: 节点执行记录 [{"node_type": ..., ...}]（最后 yield）
        """
        logger.info("=" * 80)
        logger.info(" chat_stream 方法开始执行")
        logger.info(f"  Message: {message[:100]}")
        logger.info(f"  Has tools: {bool(self.tools)}")
        logger.info(f"  Tool count: {len(self.tools) if self.tools else 0}")
        logger.info("=" * 80)

        # 收集节点执行记录
        node_executions: List[Dict[str, Any]] = []
        _tool_stack: List[Dict[str, Any]] = []

        try:
            # 准备消息列表（支持多模态）
            messages = self._prepare_messages(message, history, context, files)
            trace = AgentTraceRecorder(
                model=self.model_name,
                strategy=self.strategy,
                system_prompt=self.system_prompt,
                messages=messages,
            )

            logger.debug(
                f"准备流式调用，has_tools={bool(self.tools)}, has_files={bool(files)}, message_count={len(messages)}"
            )

            chunk_count = 0

            # 统一使用 agent 的 astream_events 实现流式输出
            logger.debug("使用 Agent astream_events 实现流式输出")
            full_content = ''
            full_reasoning = ''
            try:
                last_event = {}
                async for event in self.agent.astream_events(
                        {"messages": messages},
                        version="v2",
                        config={"recursion_limit": self.max_iterations}
                ):
                    last_event = event
                    chunk_count += 1
                    kind = event.get("event")

                    # 处理所有可能的流式事件
                    if kind in ("on_chat_model_start", "on_llm_start"):
                        trace.start_llm(event.get("data", {}).get("input"))
                        yield {"type": "agent_log", "data": trace.to_dict()}

                    elif kind in ("on_chat_model_end", "on_llm_end"):
                        output_message = self._extract_message_from_event_output(event.get("data", {}).get("output"))
                        output_text = self._content_to_text(output_message.content) if output_message else ""
                        tokens = self._extract_tokens_from_message(output_message) if output_message else 0
                        trace.finish_llm(output=output_text, tokens=tokens)
                        yield {"type": "agent_log", "data": trace.to_dict()}

                    elif kind == "on_chat_model_stream":
                        # LLM 流式输出
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content"):
                            # 提取深度思考内容（仅在启用深度思考时）
                            if self.deep_thinking:
                                reasoning_chunk = self._extract_reasoning_content(chunk)
                                if reasoning_chunk:
                                    full_reasoning += reasoning_chunk
                                    trace.append_llm_chunk(reasoning=reasoning_chunk)
                                    yield {"type": "reasoning", "content": reasoning_chunk}

                            # 处理多模态响应：content 可能是字符串或列表
                            chunk_content = chunk.content
                            if isinstance(chunk_content, str) and chunk_content:
                                full_content += chunk_content
                                trace.append_llm_chunk(output=chunk_content)
                                yield chunk_content
                            elif isinstance(chunk_content, list):
                                # 多模态响应：提取文本部分
                                for item in chunk_content:
                                    if isinstance(item, dict):
                                        # 通义千问格式: {"text": "..."}
                                        if "text" in item:
                                            text = item.get("text", "")
                                            if text:
                                                full_content += text
                                                trace.append_llm_chunk(output=text)
                                                yield text
                                        # OpenAI 格式: {"type": "text", "text": "..."}
                                        elif item.get("type") == "text":
                                            text = item.get("text", "")
                                            if text:
                                                full_content += text
                                                trace.append_llm_chunk(output=text)
                                                yield text
                                    elif isinstance(item, str):
                                        full_content += item
                                        trace.append_llm_chunk(output=item)
                                        yield item

                    elif kind == "on_llm_stream":
                        # 另一种 LLM 流式事件
                        chunk = event.get("data", {}).get("chunk")
                        if chunk:
                            if hasattr(chunk, "content"):
                                # 提取深度思考内容（仅在启用深度思考时）
                                if self.deep_thinking:
                                    reasoning_chunk = self._extract_reasoning_content(chunk)
                                    if reasoning_chunk:
                                        full_reasoning += reasoning_chunk
                                        trace.append_llm_chunk(reasoning=reasoning_chunk)
                                        yield {"type": "reasoning", "content": reasoning_chunk}

                                chunk_content = chunk.content
                                if isinstance(chunk_content, str) and chunk_content:
                                    full_content += chunk_content
                                    trace.append_llm_chunk(output=chunk_content)
                                    yield chunk_content
                                elif isinstance(chunk_content, list):
                                    # 多模态响应：提取文本部分
                                    for item in chunk_content:
                                        if isinstance(item, dict):
                                            # 通义千问格式: {"text": "..."}
                                            if "text" in item:
                                                text = item.get("text", "")
                                                if text:
                                                    full_content += text
                                                    trace.append_llm_chunk(output=text)
                                                    yield text
                                            # OpenAI 格式: {"type": "text", "text": "..."}
                                            elif item.get("type") == "text":
                                                text = item.get("text", "")
                                                if text:
                                                    full_content += text
                                                    trace.append_llm_chunk(output=text)
                                                    yield text
                                        elif isinstance(item, str):
                                            full_content += item
                                            trace.append_llm_chunk(output=item)
                                            yield item
                            elif isinstance(chunk, str):
                                full_content += chunk
                                trace.append_llm_chunk(output=chunk)
                                yield chunk

                    # 记录工具调用
                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        tool_input = event.get("data", {}).get("input")
                        step_id = str(_uuid.uuid4())
                        tool_meta = self._tool_meta_map.get(tool_name, {})
                        node = {
                            "step_id": step_id,
                            "node_type": "tool",
                            "node_name": tool_name,
                            "status": "running",
                            "input": self._truncate_data(tool_input),
                            "output": None,
                            "elapsed_time": 0,
                            "started_at": time.time(),
                            "error": None,
                            "meta": tool_meta if tool_meta else None,
                        }
                        _tool_stack.append(node)
                        node_executions.append(node)
                        trace.start_tool(
                            step_id=step_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            meta=tool_meta if tool_meta else None,
                        )
                        logger.debug(f"工具调用开始: {tool_name}")
                        # 实时推送工具调用开始事件
                        yield {"type": "agent_log", "data": trace.to_dict()}
                        yield {"type": "tool_start", "step_id": step_id, "name": tool_name, "input": self._truncate_data(tool_input), "meta": tool_meta if tool_meta else None}
                    elif kind == "on_tool_end":
                        tool_output = event.get("data", {}).get("output")
                        tool_name = event.get("name", "unknown")
                        matched_node = None
                        if _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        matched_step_id = matched_node.get("step_id") if matched_node else None
                        if matched_node:
                            matched_node["status"] = "completed"
                            matched_node["output"] = self._truncate_data(tool_output)
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )
                            # 提取工具的额外来源信息
                            self._extract_tool_sources(tool_name, matched_node)
                        trace.finish_tool(
                            step_id=matched_step_id,
                            tool_name=tool_name,
                            status="success",
                            output=tool_output,
                            meta=matched_node.get("meta") if matched_node else None,
                        )
                        logger.debug(f"工具调用结束: {tool_name}")
                        # 实时推送工具调用结束事件（含更新后的 meta）
                        yield {"type": "agent_log", "data": trace.to_dict()}
                        yield {"type": "tool_end", "step_id": matched_step_id, "name": tool_name, "output": self._truncate_data(tool_output), "meta": matched_node.get("meta") if matched_node else None}
                    elif kind == "on_tool_error":
                        error_msg = str(event.get("data", {}).get("error", ""))
                        tool_name = event.get("name", "unknown")
                        matched_node = None
                        if _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        matched_step_id = matched_node.get("step_id") if matched_node else None
                        if matched_node:
                            matched_node["status"] = "failed"
                            matched_node["error"] = error_msg[:500]
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )
                        # 实时推送工具调用失败事件
                        trace.finish_tool(
                            step_id=matched_step_id,
                            tool_name=tool_name,
                            status="error",
                            error=error_msg[:500],
                            meta=matched_node.get("meta") if matched_node else None,
                        )
                        yield {"type": "agent_log", "data": trace.to_dict()}
                        yield {"type": "tool_error", "step_id": matched_step_id, "name": tool_name, "error": error_msg[:500]}

                logger.debug(f"Agent 流式完成，共 {chunk_count} 个事件")
                # 统计token消耗
                output_messages = last_event.get("data", {}).get("output", {}).get("messages", [])
                stream_total_tokens = 0
                for msg in reversed(output_messages):
                    if isinstance(msg, AIMessage):
                        stream_total_tokens = self._extract_tokens_from_message(msg)
                        logger.info(f"流式 token 统计: total_tokens={stream_total_tokens}")
                        yield stream_total_tokens
                        break

                trace.finish_llm(output=full_content, tokens=stream_total_tokens)
                final_agent_log = trace.finalize(status="success", total_tokens=stream_total_tokens)
                yield {"type": "agent_log", "data": final_agent_log}
                yield {"type": "agent_log_final", "data": final_agent_log}

                # yield 节点执行记录（清理 started_at 并确保状态）
                for node in node_executions:
                    node.pop("started_at", None)
                    if node.get("status") == "running":
                        node["status"] = "completed"
                if node_executions:
                    yield {"type": "node_executions", "data": node_executions}

            except GraphRecursionError:
                logger.warning(
                    f"Agent 达到最大迭代次数限制 ({self.max_iterations})，模型可能不支持正确的工具调用停止判断"
                )
                if not full_content:
                    yield "抱歉，我在处理您的请求时遇到了问题（已达最大处理步骤限制）。请尝试简化问题或更换模型后重试。"
                # 标记未完成的工具为失败
                for node in _tool_stack:
                    node["status"] = "failed"
                    node["error"] = "达到最大迭代次数限制"
                    node["elapsed_time"] = round(
                        (time.time() - node.get("started_at", time.time())) * 1000, 2
                    )
                    trace.finish_tool(
                        step_id=node.get("step_id"),
                        tool_name=node.get("node_name", "unknown"),
                        status="error",
                        error=node["error"],
                        meta=node.get("meta"),
                    )
                _tool_stack.clear()
                for node in node_executions:
                    node.pop("started_at", None)
                final_agent_log = trace.finalize(
                    status="error",
                    error=f"Reached max iteration limit: {self.max_iterations}",
                )
                yield {"type": "agent_log", "data": final_agent_log}
                yield {"type": "agent_log_final", "data": final_agent_log}
                if node_executions:
                    yield {"type": "node_executions", "data": node_executions}
            except Exception as e:
                logger.error(f"Agent astream_events 失败: {str(e)}", exc_info=True)
                raise

        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"chat_stream 异常: {str(e)}")
            logger.error("=" * 80, exc_info=True)
            raise
        finally:
            logger.info("=" * 80)
            logger.info("chat_stream 方法执行结束")
            logger.info("=" * 80)
