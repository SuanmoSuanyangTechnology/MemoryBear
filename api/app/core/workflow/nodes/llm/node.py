"""
LLM 节点实现

调用 LLM 模型进行文本生成。
"""

import logging
from typing import Any
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.models import RedBearLLM, RedBearModelConfig
from app.db import get_db_context
from app.models import ModelType
from app.services.model_service import ModelConfigService
        
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

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
    
    def _prepare_llm(self, state: WorkflowState,stream:bool = False) -> tuple[RedBearLLM, list | str]:
        """准备 LLM 实例（公共逻辑）
        
        Args:
            state: 工作流状态
        
        Returns:
            (llm, messages_or_prompt): LLM 实例和消息列表或 prompt 字符串
        """

        # 1. 处理消息格式（优先使用 messages）
        messages_config = self.config.get("messages")
        
        if messages_config:
            # 使用 LangChain 消息格式
            messages = []
            for msg_config in messages_config:
                role = msg_config.get("role", "user").lower()
                content_template = msg_config.get("content", "")
                content = self._render_template(content_template, state)
                
                # 根据角色创建对应的消息对象
                if role == "system":
                    messages.append(SystemMessage(content=content))
                elif role in ["user", "human"]:
                    messages.append(HumanMessage(content=content))
                elif role in ["ai", "assistant"]:
                    messages.append(AIMessage(content=content))
                else:
                    logger.warning(f"未知的消息角色: {role}，默认使用 user")
                    messages.append(HumanMessage(content=content))
            
            prompt_or_messages = messages
        else:
            # 使用简单的 prompt 格式（向后兼容）
            prompt_template = self.config.get("prompt", "")
            prompt_or_messages = self._render_template(prompt_template, state)

        # 2. 获取模型配置
        model_id = self.config.get("model_id")
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
        
        return llm, prompt_or_messages
    
    async def execute(self, state: WorkflowState) -> AIMessage:
        """非流式执行 LLM 调用
        
        Args:
            state: 工作流状态
        
        Returns:
            LLM 响应消息
        """
        llm, prompt_or_messages = self._prepare_llm(state,True)
        
        logger.info(f"节点 {self.node_id} 开始执行 LLM 调用（非流式）")
        
        # 调用 LLM（支持字符串或消息列表）
        response = await llm.ainvoke(prompt_or_messages)
        # 提取内容
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = str(response)
        
        logger.info(f"节点 {self.node_id} LLM 调用完成，输出长度: {len(content)}")
        
        # 返回 AIMessage（包含响应元数据）
        return response if isinstance(response, AIMessage) else AIMessage(content=content)
    
    def _extract_input(self, state: WorkflowState) -> dict[str, Any]:
        """提取输入数据（用于记录）"""
        _, prompt_or_messages = self._prepare_llm(state)
        
        return {
            "prompt": prompt_or_messages if isinstance(prompt_or_messages, str) else None,
            "messages": [
                {"role": msg.__class__.__name__.replace("Message", "").lower(), "content": msg.content}
                for msg in prompt_or_messages
            ] if isinstance(prompt_or_messages, list) else None,
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
                    "prompt_tokens": usage.get('prompt_tokens', 0),
                    "completion_tokens": usage.get('completion_tokens', 0),
                    "total_tokens": usage.get('total_tokens', 0)
                }
        return None
    
    async def execute_stream(self, state: WorkflowState):
        """流式执行 LLM 调用
        
        Args:
            state: 工作流状态
        
        Yields:
            文本片段（chunk）或完成标记
        """
        from langgraph.config import get_stream_writer
        
        llm, prompt_or_messages = self._prepare_llm(state, True)
        
        logger.info(f"节点 {self.node_id} 开始执行 LLM 调用（流式）")
        logger.debug(f"LLM 配置: streaming={getattr(llm._model, 'streaming', 'unknown')}")
        
        # 检查是否有注入的 End 节点前缀配置
        writer = get_stream_writer()
        end_prefix = getattr(self, '_end_node_prefix', None)
        
        logger.info(f"[LLM前缀] 节点 {self.node_id} 检查前缀配置: {end_prefix is not None}")
        if end_prefix:
            logger.info(f"[LLM前缀] 前缀内容: '{end_prefix}'")
        
        if end_prefix:
            # 渲染前缀（可能包含其他变量）
            try:
                rendered_prefix = self._render_template(end_prefix, state)
                logger.info(f"节点 {self.node_id} 提前发送 End 节点前缀: '{rendered_prefix[:50]}...'")
                
                # 提前发送 End 节点的前缀（使用 "message" 类型）
                writer({
                    "type": "message",  # End 相关的内容都是 message 类型
                    "node_id": "end",  # 标记为 end 节点的输出
                    "chunk": rendered_prefix,
                    "full_content": rendered_prefix,
                    "chunk_index": 0,
                    "is_prefix": True  # 标记这是前缀
                })
            except Exception as e:
                logger.warning(f"渲染/发送 End 节点前缀失败: {e}")
        
        # 累积完整响应
        full_response = ""
        last_chunk = None
        chunk_count = 0
        
        # 调用 LLM（流式，支持字符串或消息列表）
        async for chunk in llm.astream(prompt_or_messages):
            # 提取内容
            if hasattr(chunk, 'content'):
                content = chunk.content
            else:
                content = str(chunk)
            
            # 只有当内容不为空时才处理
            if content:
                full_response += content
                last_chunk = chunk
                chunk_count += 1
                
                # 流式返回每个文本片段
                yield content
        
        logger.info(f"节点 {self.node_id} LLM 调用完成，输出长度: {len(full_response)}, 总 chunks: {chunk_count}")
        
        # 构建完整的 AIMessage（包含元数据）
        if isinstance(last_chunk, AIMessage):
            final_message = AIMessage(
                content=full_response,
                response_metadata=last_chunk.response_metadata if hasattr(last_chunk, 'response_metadata') else {}
            )
        else:
            final_message = AIMessage(content=full_response)
        
        # yield 完成标记
        yield {"__final__": True, "result": final_message}
