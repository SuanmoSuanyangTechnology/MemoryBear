"""
Embedder 客户端抽象基类

提供统一的嵌入向量生成接口，支持重试机制和错误处理。
"""

from abc import ABC, abstractmethod
from typing import List, Optional
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


class EmbedderClientException(BusinessException):
    """Embedder 客户端异常"""
    def __init__(self, message: str, code: str = BizCode.EMBEDDING_ERROR):
        super().__init__(message, code=code)


class EmbedderClient(ABC):
    """
    Embedder 客户端抽象基类

    提供统一的嵌入向量生成接口，包括：
    - 批量文本嵌入（response）
    - 自动重试机制
    - 错误处理
    """

    def __init__(self, model_config: RedBearModelConfig):
        """
        初始化 Embedder 客户端

        Args:
            model_config: 模型配置，包含模型名称、提供商、API密钥等信息
        """
        self.config = model_config
        self.model_name = model_config.model_name
        self.provider = model_config.provider
        self.api_key = model_config.api_key
        self.base_url = model_config.base_url
        self.max_retries = model_config.max_retries
        self.timeout = model_config.timeout

        logger.info(
            f"初始化 Embedder 客户端: provider={self.provider}, "
            f"model={self.model_name}, max_retries={self.max_retries}"
        )

    @abstractmethod
    async def response(
        self,
        messages: List[str],
        **kwargs
    ) -> List[List[float]]:
        """
        生成嵌入向量

        Args:
            messages: 文本列表
            **kwargs: 额外参数

        Returns:
            嵌入向量列表，每个向量是一个浮点数列表

        Raises:
            EmbedderClientException: 嵌入向量生成失败
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

    async def response_with_retry(
        self,
        messages: List[str],
        **kwargs
    ) -> List[List[float]]:
        """
        带重试机制的嵌入向量生成接口

        Args:
            messages: 文本列表
            **kwargs: 额外参数

        Returns:
            嵌入向量列表

        Raises:
            EmbedderClientException: 重试失败后抛出
        """
        retry_decorator = self._create_retry_decorator()

        @retry_decorator
        async def _response_with_retry():
            try:
                return await self.response(messages, **kwargs)
            except Exception as e:
                logger.error(f"嵌入向量生成失败: {e}")
                raise EmbedderClientException(f"嵌入向量生成失败: {e}") from e

        return await _response_with_retry()

    async def embed_single(self, text: str, **kwargs) -> List[float]:
        """
        为单个文本生成嵌入向量

        Args:
            text: 单个文本
            **kwargs: 额外参数

        Returns:
            嵌入向量（浮点数列表）

        Raises:
            EmbedderClientException: 嵌入向量生成失败
        """
        embeddings = await self.response_with_retry([text], **kwargs)
        return embeddings[0] if embeddings else []

    async def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
        **kwargs
    ) -> List[List[float]]:
        """
        批量生成嵌入向量（支持大批量文本）

        Args:
            texts: 文本列表
            batch_size: 每批处理的文本数量
            **kwargs: 额外参数

        Returns:
            嵌入向量列表

        Raises:
            EmbedderClientException: 嵌入向量生成失败
        """
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = await self.response_with_retry(batch, **kwargs)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings
