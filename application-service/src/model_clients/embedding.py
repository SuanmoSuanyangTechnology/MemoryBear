"""Embedding shell (remote mode): the host only holds a config reference; credentials, routing and failover live in the model service.

Runtime calls are always constructed via ``for_invoke(ref)`` -> ``POST /internal/v1/invoke``;
text results come back at their original indices with ``None`` for blank slots; multimodal
variants are not yet served by the model service and currently always raise
``NotImplementedError``.
"""

from typing import Any, Dict, List, Union

from langchain_core.embeddings import Embeddings

from ..integrations.model.invoke_backend import (
    RemoteInvokeRef,
    acall_embedding,
    call_embedding_sync,
)


class RedBearEmbeddings(Embeddings):
    """Unified embedding shell: the sole construction entry is ``for_invoke(ref)``.

    The host no longer holds an api_key / model instance (the shell object carries only
    the config reference).
    """

    @classmethod
    def for_invoke(cls, ref: RemoteInvokeRef) -> "RedBearEmbeddings":
        """Remote mode: the host no longer holds credentials; calls carry only the config id and tenant."""
        instance = cls.__new__(cls)
        instance._remote = ref
        return instance

    def _unsupported(self, feature: str) -> NotImplementedError:
        # The host holds no credentials and cannot determine multimodal capability:
        # multimodal variants are not yet served by the model service
        return NotImplementedError(f"{feature}在远端模式下不可用（多模态随 G4 接入模型服务）")

    # ==================== LangChain standard interface ====================

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch text embedding (LangChain standard interface); blank slots return ``None``."""
        return call_embedding_sync(self._remote, texts)  # type: ignore[return-value]

    def embed_query(self, text: str) -> List[float]:
        """Single text embedding (LangChain standard interface)."""
        result = call_embedding_sync(self._remote, [text])
        return result[0] or []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch text embedding (async); blank slots return ``None``."""
        return await acall_embedding(self._remote, texts)  # type: ignore[return-value]

    async def aembed_query(self, text: str) -> List[float]:
        """Single text embedding (async)."""
        result = await acall_embedding(self._remote, [text])
        return result[0] or []

    # ==================== Multimodal extension methods ====================

    def embed_multimodal(
        self,
        contents: List[Dict[str, Any]],
        **kwargs
    ) -> List[List[float]]:
        """Multimodal embedding (not yet served by the model service)."""
        raise self._unsupported("多模态 Embedding")

    async def aembed_multimodal(
        self,
        contents: List[Dict[str, Any]],
        **kwargs
    ) -> List[List[float]]:
        """Async multimodal embedding (not yet served by the model service)."""
        raise self._unsupported("多模态 Embedding")

    def embed_text(self, text: str, **kwargs) -> List[float]:
        """Text embedding (convenience method)."""
        return self.embed_query(text)

    def embed_image(self, image_url: str, **kwargs) -> List[float]:
        """Image embedding (not yet served by the model service)."""
        raise self._unsupported("图片向量化")

    def embed_video(self, video_url: str, **kwargs) -> List[float]:
        """Video embedding (not yet served by the model service)."""
        raise self._unsupported("视频向量化")

    def embed_batch(
        self,
        items: List[Union[str, Dict[str, Any]]],
        **kwargs
    ) -> List[List[float]]:
        """Batch embedding: all-string input uses the standard method; mixed types are not yet served."""
        if all(isinstance(item, str) for item in items):
            return self.embed_documents(items)
        raise self._unsupported("混合类型批量向量化")

    # ==================== Utility methods ====================

    def is_multimodal_supported(self) -> bool:
        """The host remote mode is provider-agnostic and never supports multimodal input."""
        return False
