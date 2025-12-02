"""
LLM 客户端抽象基类

提供统一的 LLM 调用接口，支持重试机制和错误处理。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import asyncio
import logging
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from app.core.models.base import RedBearModelConfig
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode

logger = logging.getLogger(__name__)


class LLMClientException(BusinessException):
    """LLM 客户端异常"""
    def __init__(self, message: str, code: str = BizCode.LLM_ERROR):
        super().__init__(message, code=code)


class LLMClient(ABC):
    """
    LLM 客户端抽象基类

    提供统一的 LLM 调用接口，包括：
    - 聊天接口（chat）
    - 结构化输出接口（response_structured）
    - 自动重试机制
    - 错误处理
    """

    def __init__(self, model_config: RedBearModelConfig):
        """
        初始化 LLM 客户端

        Args:
            model_config: 模型配置，包含模型名称、提供商、API密钥等信息
        """
        self.config = model_config
        self.model_name = self.config.model_name
        self.provider = self.config.provider
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self.max_retries = self.config.max_retries
        self.timeout = self.config.timeout

        logger.info(
            f"初始化 LLM 客户端: provider={self.provider}, "
            f"model={self.model_name}, max_retries={self.max_retries}"
        )

    @abstractmethod
    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> Any:
        """
        聊天接口

        Args:
            messages: 消息列表，每个消息包含 role 和 content
            **kwargs: 额外参数

        Returns:
            LLM 响应内容

        Raises:
            LLMClientException: LLM 调用失败
        """
        pass

    @abstractmethod
    async def response_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: type[BaseModel],
        **kwargs
    ) -> BaseModel:
        """
        结构化输出接口

        Args:
            messages: 消息列表
            response_model: 期望的响应模型类型（Pydantic BaseModel）
            **kwargs: 额外参数

        Returns:
            解析后的 Pydantic 模型实例

        Raises:
            LLMClientException: LLM 调用或解析失败
        """
        pass

    def _create_retry_decorator(self):
        """
        创建重试装饰器

        Returns:
            配置好的 tenacity retry 装饰器
        """
        return retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((
                asyncio.TimeoutError,
                ConnectionError,
                Exception,  # 可以根据需要细化异常类型
            )),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    async def chat_with_retry(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> Any:
        """
        带重试机制的聊天接口

        Args:
            messages: 消息列表
            **kwargs: 额外参数

        Returns:
            LLM 响应内容

        Raises:
            LLMClientException: 重试失败后抛出
        """
        retry_decorator = self._create_retry_decorator()

        @retry_decorator
        async def _chat_with_retry():
            try:
                return await self.chat(messages, **kwargs)
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}")
                raise LLMClientException(f"LLM 调用失败: {e}") from e

        return await _chat_with_retry()

    async def response_structured_with_retry(
        self,
        messages: List[Dict[str, str]],
        response_model: type[BaseModel],
        **kwargs
    ) -> BaseModel:
        """
        带重试机制的结构化输出接口

        Args:
            messages: 消息列表
            response_model: 期望的响应模型类型
            **kwargs: 额外参数

        Returns:
            解析后的 Pydantic 模型实例

        Raises:
            LLMClientException: 重试失败后抛出
        """
        retry_decorator = self._create_retry_decorator()

        @retry_decorator
        async def _response_structured_with_retry():
            try:
                return await self.response_structured(
                    messages,
                    response_model,
                    **kwargs
                )
            except Exception as e:
                logger.error(f"LLM 结构化输出失败: {e}")
                raise LLMClientException(f"LLM 结构化输出失败: {e}") from e

        return await _response_structured_with_retry()
