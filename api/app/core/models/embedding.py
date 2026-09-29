"""Embedding 壳（远端模式）：宿主只持有配置引用，凭据、选路与 failover 在模型服务。

运行面调用一律经 ``for_invoke(ref)`` 构造 → ``POST /internal/v1/invoke``（设计 §2.2），
文本结果按原下标返回、空白位为 ``None``；多模态族随 G4 接入服务侧，当前恒
``NotImplementedError``。
"""

from typing import Any, Dict, List, Union

from langchain_core.embeddings import Embeddings

from app.integrations.model.invoke_backend import (
    RemoteInvokeRef,
    acall_embedding,
    call_embedding_sync,
)


class RedBearEmbeddings(Embeddings):
    """统一的 Embedding 壳：唯一构造入口 ``for_invoke(ref)``。

    宿主不再持有 api_key / 模型实例（验收：壳对象只有配置引用）。
    """

    @classmethod
    def for_invoke(cls, ref: RemoteInvokeRef) -> "RedBearEmbeddings":
        """远端模式：宿主不再持有凭据（设计 §2.2），调用只带配置 id 与租户。"""
        instance = cls.__new__(cls)
        instance._remote = ref
        return instance

    def _unsupported(self, feature: str) -> NotImplementedError:
        # 宿主不持有凭据、也无法判定多模态能力：多模态族随 G4 接入服务侧
        return NotImplementedError(f"{feature}在远端模式下不可用（多模态随 G4 接入模型服务）")

    # ==================== LangChain 标准接口 ====================

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化（LangChain 标准接口）；空白位返回 ``None``。"""
        return call_embedding_sync(self._remote, texts)  # type: ignore[return-value]

    def embed_query(self, text: str) -> List[float]:
        """单个文本向量化（LangChain 标准接口）"""
        result = call_embedding_sync(self._remote, [text])
        return result[0] or []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化（异步）；空白位返回 ``None``。"""
        return await acall_embedding(self._remote, texts)  # type: ignore[return-value]

    async def aembed_query(self, text: str) -> List[float]:
        """单个文本向量化（异步）"""
        result = await acall_embedding(self._remote, [text])
        return result[0] or []

    # ==================== 多模态扩展方法 ====================

    def embed_multimodal(
        self,
        contents: List[Dict[str, Any]],
        **kwargs
    ) -> List[List[float]]:
        """多模态向量化（随 G4 接入模型服务）"""
        raise self._unsupported("多模态 Embedding")

    async def aembed_multimodal(
        self,
        contents: List[Dict[str, Any]],
        **kwargs
    ) -> List[List[float]]:
        """异步多模态向量化（随 G4 接入模型服务）"""
        raise self._unsupported("多模态 Embedding")

    def embed_text(self, text: str, **kwargs) -> List[float]:
        """文本向量化（便捷方法）"""
        return self.embed_query(text)

    def embed_image(self, image_url: str, **kwargs) -> List[float]:
        """图片向量化（随 G4 接入模型服务）"""
        raise self._unsupported("图片向量化")

    def embed_video(self, video_url: str, **kwargs) -> List[float]:
        """视频向量化（随 G4 接入模型服务）"""
        raise self._unsupported("视频向量化")

    def embed_batch(
        self,
        items: List[Union[str, Dict[str, Any]]],
        **kwargs
    ) -> List[List[float]]:
        """批量向量化：全字符串走标准方法，混合类型随 G4 接入模型服务。"""
        if all(isinstance(item, str) for item in items):
            return self.embed_documents(items)
        raise self._unsupported("混合类型批量向量化")

    # ==================== 工具方法 ====================

    def is_multimodal_supported(self) -> bool:
        """宿主远端模式不感知 provider，恒不支持多模态"""
        return False
