"""
Embedder 客户端抽象基类

提供统一的嵌入向量生成接口，支持重试机制和错误处理。
"""

from abc import ABC, abstractmethod
from typing import List
import asyncio
import logging
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.integrations.model.invoke_backend import RemoteInvokeRef

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

    运行面只有远端模式：``remote`` 非解密配置引用，凭据解密、重试与选路都在模型服务，
    宿主不持有 api_key。
    """

    def __init__(self, *, remote: RemoteInvokeRef):
        """
        初始化 Embedder 客户端

        Args:
            remote: 非解密配置引用（宿主不持有凭据）
        """
        self.remote = remote
        # 宿主不感知 provider/模型名（设计 §2.2），重试在模型服务，宿主不叠加退避
        self.model_name = ""
        self.provider = ""
        self.api_key = ""
        self.base_url = None
        self.max_retries = 1
        self.timeout = None
        logger.info(f"初始化远端 Embedder 客户端: config_id={remote.config_id}")

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
