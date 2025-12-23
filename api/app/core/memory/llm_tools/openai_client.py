"""
OpenAI LLM 客户端实现

基于 LangChain 和 RedBearLLM 的 OpenAI 客户端实现。
"""

import asyncio
import json
import logging
from typing import Any, Dict, List

from app.core.config import settings
from app.core.memory.llm_tools.llm_client import LLMClient, LLMClientException
from app.core.models.base import RedBearModelConfig
from app.core.models.llm import RedBearLLM
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    """
    OpenAI LLM 客户端实现

    基于 LangChain 和 RedBearLLM 的实现，支持：
    - 聊天接口
    - 结构化输出
    - Langfuse 追踪（可选）
    """

    def __init__(self, model_config: RedBearModelConfig, type_: str = "chat"):
        """
        初始化 OpenAI 客户端

        Args:
            model_config: 模型配置
            type_: 模型类型，"chat" 或 "completion"
        """
        super().__init__(model_config)

        # 初始化 Langfuse 回调处理器（如果启用）
        self.langfuse_handler = None
        if settings.LANGFUSE_ENABLED:
            try:
                from langfuse.langchain import CallbackHandler
                self.langfuse_handler = CallbackHandler()
                logger.info("Langfuse 追踪已启用")
            except ImportError:
                logger.warning("Langfuse 未安装，跳过追踪功能")
            except Exception as e:
                logger.warning(f"初始化 Langfuse 处理器失败: {e}")

        # 初始化 RedBearLLM 客户端
        self.client = RedBearLLM(
            RedBearModelConfig(
                model_name=self.model_name,
                provider=self.provider,
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=self.max_retries,
                timeout=self.timeout,
            ),
            type=type_
        )

        logger.info(f"OpenAI 客户端初始化完成: type={type_}")

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> Any:
        """
        聊天接口实现

        Args:
            messages: 消息列表
            **kwargs: 额外参数

        Returns:
            LLM 响应内容

        Raises:
            LLMClientException: LLM 调用失败
        """
        try:
            template = """{messages}"""
            prompt = ChatPromptTemplate.from_template(template)
            chain = prompt | self.client

            # 添加 Langfuse 回调（如果可用）
            config = {}
            if self.langfuse_handler:
                config["callbacks"] = [self.langfuse_handler]

            response = await chain.ainvoke({"messages": messages}, config=config)

            logger.debug(f"LLM 响应成功: {len(str(response))} 字符")
            return response

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise LLMClientException(f"LLM 调用失败: {e}") from e

    async def response_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: type[BaseModel],
        **kwargs
    ) -> BaseModel:
        """
        结构化输出接口实现

        Args:
            messages: 消息列表
            response_model: 期望的响应模型类型
            **kwargs: 额外参数

        Returns:
            解析后的 Pydantic 模型实例

        Raises:
            LLMClientException: LLM 调用或解析失败
        """
        try:
            # 构建问题文本
            question_text = "\n\n".join([
                str(m.get("content", "")) for m in messages
            ])

            # 准备配置（包含 Langfuse 回调）
            config = {}
            if self.langfuse_handler:
                config["callbacks"] = [self.langfuse_handler]

            # 方法 1: 使用 PydanticOutputParser
            if PydanticOutputParser is not None:
                try:
                    parser = PydanticOutputParser(pydantic_object=response_model)
                    format_instructions = parser.get_format_instructions()
                    prompt = ChatPromptTemplate.from_template(
                        "{question}\n{format_instructions}"
                    )
                    chain = prompt | self.client | parser

                    parsed = await chain.ainvoke(
                        {
                            "question": question_text,
                            "format_instructions": format_instructions,
                        },
                        config=config
                    )

                    logger.debug(f"使用 PydanticOutputParser 解析成功")
                    return parsed

                except Exception as e:
                    logger.warning(
                        f"PydanticOutputParser 解析失败，尝试其他方法: {e}"
                    )

            # 方法 2: 使用 LangChain 的 with_structured_output
            template = """{question}"""
            prompt = ChatPromptTemplate.from_template(template)

            try:
                with_so = getattr(self.client, "with_structured_output", None)

                if callable(with_so):
                    structured_chain = prompt | with_so(response_model, strict=True)
                    parsed = await structured_chain.ainvoke(
                        {"question": question_text},
                        config=config
                    )

                    # 验证并返回结果
                    try:
                        return response_model.model_validate(parsed)
                    except Exception:
                        # 如果已经是 Pydantic 实例，直接返回
                        if hasattr(parsed, "model_dump"):
                            return parsed
                        # 尝试从 JSON 解析
                        return response_model.model_validate_json(json.dumps(parsed))

            except Exception as e:
                logger.error(f"结构化输出失败: {e}")
                raise LLMClientException(f"结构化输出失败: {e}") from e

            # 如果所有方法都失败，抛出异常
            raise LLMClientException(
                "无法生成结构化输出，所有解析方法均失败"
            )

        except LLMClientException:
            raise
        except Exception as e:
            logger.error(f"结构化输出处理失败: {e}")
            raise LLMClientException(f"结构化输出处理失败: {e}") from e
