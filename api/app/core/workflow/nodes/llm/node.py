"""
LLM 节点实现

调用 LLM 模型进行文本生成。
"""

import logging
import re
from typing import Any

from langchain_core.messages import AIMessage

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.llm.config import LLMNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool
from app.db import get_db_context
from app.models import ModelType
from app.services.model_service import ModelConfigService

logger = logging.getLogger(__name__)


class LLMNode(BaseNode):
    """LLM 节点
    
    支持流式和非流式输出，使用 LangChain 标准的消息格式。
    
    配置示例（支持多种消息格式）:
    
    1. 简单文本格式：
    {
        "type": "llm",
        "config": {
            "model_id": "uuid",
            "prompt": "请分析：{{sys.message}}",
            "temperature": 0.7,
            "max_tokens": 1000
        }
    }
    
    2. LangChain 消息格式（推荐）：
    {
        "type": "llm",
        "config": {
            "model_id": "uuid",
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个专业的 AI 助手。"
                },
                {
                    "role": "user",
                    "content": "{{sys.message}}"
                }
            ],
            "temperature": 0.7,
            "max_tokens": 1000
        }
    }
    
    支持的角色类型：
    - system: 系统消息（SystemMessage）
    - user/human: 用户消息（HumanMessage）
    - ai/assistant: AI 消息（AIMessage）
    """

    def _output_types(self) -> dict[str, VariableType]:
        return {"output": VariableType.STRING}

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: LLMNodeConfig | None = None

    def _render_context(self, message: str, variable_pool: VariablePool):
        context = f"<context>{self._render_template(self.typed_config.context, variable_pool)}</context>"
        return re.sub(r"{{context}}", context, message)

    async def _prepare_llm(
            self,
            state: WorkflowState,
            variable_pool: VariablePool,
            stream: bool = False
    ) -> RedBearLLM:
        """准备 LLM 实例（公共逻辑）
        
        Args:
            variable_pool: 变量池
        
        Returns:
            (llm, messages_or_prompt): LLM 实例和消息列表或 prompt 字符串
        """
        self.typed_config = LLMNodeConfig(**self.config)

        model_id = self.typed_config.model_id
        if not model_id:
            raise ValueError(f"节点 {self.node_id} 缺少 model_id 配置")

        # 3. 在 with 块内完成所有数据库操作和数据提取
        with get_db_context() as db:
            config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)

            if not config:
                raise BusinessException("配置的模型不存在", BizCode.NOT_FOUND)

            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)

            # 在 Session 关闭前提取所有需要的数据
            api_config = config.api_keys[0]
            model_name = api_config.model_name
            provider = api_config.provider
            api_key = api_config.api_key
            api_base = api_config.api_base
            model_type = config.type

        # 4. 创建 LLM 实例（使用已提取的数据）
        # 注意：对于流式输出，需要在模型初始化时设置 streaming=True
        extra_params = {"streaming": stream} if stream else {}

        llm = RedBearLLM(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
                extra_params=extra_params
            ),
            type=ModelType(model_type)
        )

        logger.debug(f"创建 LLM 实例: provider={provider}, model={model_name}, streaming={stream}")

        messages_config = self.typed_config.messages

        if messages_config:
            # 使用 LangChain 消息格式
            messages = []
            for msg_config in messages_config:
                role = msg_config.role.lower()
                content_template = msg_config.content
                content_template = self._render_context(content_template, variable_pool)
                content = self._render_template(content_template, variable_pool)

                # 根据角色创建对应的消息对象
                if role == "system":
                    messages.append({
                        "role": "system",
                        "content": content
                    })
                elif role in ["user", "human"]:
                    messages.append({
                        "role": "user",
                        "content": content
                    })
                elif role in ["ai", "assistant"]:
                    messages.append({
                        "role": "assistant",
                        "content": content
                    })
                else:
                    logger.warning(f"未知的消息角色: {role}，默认使用 user")
                    messages.append({
                        "role": "user",
                        "content": content
                    })

            if self.typed_config.vision_input and self.typed_config.vision:
                file_content = []
                files = variable_pool.get_value(self.typed_config.vision_input)
                for file in files:
                    content = await self.process_message(provider, file, self.typed_config.vision)
                    if content:
                        file_content.append(content)
                if messages and messages[-1]["role"] == 'user':
                    messages[-1]['content'] = [messages[-1]["content"]] + file_content
                else:
                    messages.append({"role": "user", "content": file_content})

            if self.typed_config.memory.enable:
                messages = messages[:-1] + state["messages"][-self.typed_config.memory.window_size:] + messages[-1:]
            self.messages = messages
        else:
            # 使用简单的 prompt 格式（向后兼容）
            prompt_template = self.config.get("prompt", "")
            self.messages = self._render_template(prompt_template, variable_pool)

        return llm

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> AIMessage:
        """非流式执行 LLM 调用
        
        Args:
            state: 工作流状态
            variable_pool: 变量池
        
        Returns:
            LLM 响应消息
        """
        # self.typed_config = LLMNodeConfig(**self.config)
        llm = await self._prepare_llm(state, variable_pool, False)

        logger.info(f"节点 {self.node_id} 开始执行 LLM 调用（非流式）")

        # 调用 LLM（支持字符串或消息列表）
        response = await llm.ainvoke(self.messages)
        # 提取内容
        if hasattr(response, 'content'):
            content = self.process_model_output(response.content)
        else:
            content = str(response)

        logger.info(f"节点 {self.node_id} LLM 调用完成，输出长度: {len(content)}")

        # 返回 AIMessage（包含响应元数据）
        return AIMessage(content=content, response_metadata=response.response_metadata)

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """提取输入数据（用于记录）"""

        return {
            "prompt": self.messages if isinstance(self.messages, str) else None,
            "messages": [
                {"role": msg.get("role"), "content": msg.get("content", "")}
                for msg in self.messages
            ] if isinstance(self.messages, list) else None,
            "config": {
                "model_id": self.config.get("model_id"),
                "temperature": self.config.get("temperature"),
                "max_tokens": self.config.get("max_tokens")
            }
        }

    def _extract_output(self, business_result: Any) -> str:
        """从 AIMessage 中提取文本内容"""
        if isinstance(business_result, AIMessage):
            return business_result.content
        return str(business_result)

    def _extract_token_usage(self, business_result: Any) -> dict[str, int] | None:
        """从 AIMessage 中提取 token 使用情况"""
        if isinstance(business_result, AIMessage) and hasattr(business_result, 'response_metadata'):
            usage = business_result.response_metadata.get('token_usage')
            if usage:
                return {
                    "prompt_tokens": usage.get('input_tokens', 0),
                    "completion_tokens": usage.get('output_tokens', 0),
                    "total_tokens": usage.get('total_tokens', 0)
                }
        return None

    async def execute_stream(self, state: WorkflowState, variable_pool: VariablePool):
        """流式执行 LLM 调用
        
        Args:
            state: 工作流状态
            variable_pool: 变量池
        
        Yields:
            文本片段（chunk）或完成标记
        """
        self.typed_config = LLMNodeConfig(**self.config)

        llm = await self._prepare_llm(state, variable_pool, True)

        logger.info(f"节点 {self.node_id} 开始执行 LLM 调用（流式）")
        logger.debug(f"LLM 配置: streaming={getattr(llm._model, 'streaming', 'unknown')}")

        # 累积完整响应
        full_response = ""
        chunk_count = 0

        # 调用 LLM（流式，支持字符串或消息列表）
        last_meta_data = {}
        async for chunk in llm.astream(self.messages, stream_usage=True):
            # 提取内容
            if hasattr(chunk, 'content'):
                content = self.process_model_output(chunk.content)
            else:
                content = str(chunk)
            if hasattr(chunk, 'response_metadata'):
                if chunk.response_metadata:
                    last_meta_data = chunk.response_metadata

            # 只有当内容不为空时才处理
            if content:
                full_response += content
                chunk_count += 1

                # 流式返回每个文本片段
                yield {
                    "__final__": False,
                    "chunk": content
                }

        yield {
            "__final__": False,
            "chunk": "",
            "done": True
        }
        logger.info(f"节点 {self.node_id} LLM 调用完成，输出长度: {len(full_response)}, 总 chunks: {chunk_count}")

        # 构建完整的 AIMessage（包含元数据）
        final_message = AIMessage(
            content=full_response,
            response_metadata=last_meta_data
        )

        # yield 完成标记
        yield {"__final__": True, "result": final_message}
