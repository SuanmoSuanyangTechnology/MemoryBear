"""
OpenAI Embedder 客户端实现

基于 LangChain 和 RedBearEmbeddings 的 OpenAI 嵌入模型客户端实现。
自动支持火山引擎的多模态 Embedding。
"""

from typing import List
import logging

from app.core.memory.llm_tools.embedder_client import (
    EmbedderClient,
    EmbedderClientException
)
from app.core.models.base import RedBearModelConfig
from app.core.models.embedding import RedBearEmbeddings
from app.models.models_model import ModelProvider

logger = logging.getLogger(__name__)


class OpenAIEmbedderClient(EmbedderClient):
    """
    OpenAI Embedder 客户端实现

    基于 LangChain 和 RedBearEmbeddings 的实现，支持：
    - 批量文本嵌入
    - 自动重试机制
    - 错误处理
    - 火山引擎多模态 Embedding（自动识别）
    """

    def __init__(self, model_config: RedBearModelConfig):
        """
        初始化 OpenAI Embedder 客户端

        Args:
            model_config: 模型配置
        """
        super().__init__(model_config)

        # 初始化 RedBearEmbeddings（自动支持火山引擎多模态）
        self.model = RedBearEmbeddings(
            RedBearModelConfig(
                model_name=self.model_name,
                provider=self.provider,
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=self.max_retries,
                timeout=self.timeout,
            )
        )
        self.is_multimodal = self.model.is_multimodal_supported()

        logger.info(f"OpenAI Embedder 客户端初始化完成 (provider={self.provider}, multimodal={self.is_multimodal})")

    async def response(
        self,
        messages: List[str],
        **kwargs
    ) -> List[List[float]]:
        """
        生成嵌入向量实现

        Args:
            messages: 文本列表
            **kwargs: 额外参数

        Returns:
            嵌入向量列表

        Raises:
            EmbedderClientException: 嵌入向量生成失败
        """
        try:
            # 过滤空文本
            texts: List[str] = [str(m) for m in messages if m is not None]

            if not texts:
                logger.warning("输入文本列表为空，返回空结果")
                return []

            # 生成嵌入向量
            if self.is_multimodal:
                # 火山引擎多模态 Embedding
                embeddings = await self.model.aembed_multimodal(
                    [{"type": "text", "text": text} for text in texts]
                )
            else:
                # 普通 Embedding
                embeddings = await self.model.aembed_documents(texts)

            logger.debug(f"成功生成 {len(embeddings)} 个嵌入向量")
            return embeddings

        except Exception as e:
            logger.error(f"嵌入向量生成失败: {e}")
            raise EmbedderClientException(f"嵌入向量生成失败: {e}") from e
