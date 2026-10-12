"""
OpenAI Embedder 客户端实现

基于 RedBearEmbeddings 远端壳的嵌入模型客户端实现。
"""

from typing import List
import logging

from app.core.memory.llm_tools.embedder_client import (
    EmbedderClient,
    EmbedderClientException
)
from app.core.models.embedding import RedBearEmbeddings
from app.integrations.model.invoke_backend import RemoteInvokeRef

logger = logging.getLogger(__name__)


class OpenAIEmbedderClient(EmbedderClient):
    """
    OpenAI Embedder 客户端实现

    基于 RedBearEmbeddings 远端壳的实现，支持：
    - 批量文本嵌入
    - 错误处理

    凭据解密、渠道选路与重试都在模型服务侧（宿主只持有非解密引用）；
    多模态 Embedding 随 G4 接入服务侧。
    """

    def __init__(self, *, remote: RemoteInvokeRef):
        """
        初始化 OpenAI Embedder 客户端

        Args:
            remote: 非解密配置引用（凭据在模型服务）
        """
        super().__init__(remote=remote)

        # 远端壳：宿主不持有凭据
        self.model = RedBearEmbeddings.for_invoke(remote)

        logger.info(f"OpenAI Embedder 客户端初始化完成 (remote, config_id={remote.config_id})")

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
            嵌入向量列表；入参中的空白文本按位返回 ``None``（与入参等长，
            调用方自行判定）

        Raises:
            EmbedderClientException: 嵌入向量生成失败
        """
        try:
            # 过滤空文本
            texts: List[str] = [str(m) for m in messages if m is not None]

            if not texts:
                logger.warning("输入文本列表为空，返回空结果")
                return []

            embeddings = await self.model.aembed_documents(texts)

            logger.debug(f"成功生成 {len(embeddings)} 个嵌入向量")
            return embeddings

        except Exception as e:
            logger.error(f"嵌入向量生成失败: {e}")
            raise EmbedderClientException(f"嵌入向量生成失败: {e}") from e
