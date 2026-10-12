"""Embedding 壳（远端模式，G4b）：km 只持有配置引用，凭据、选路与 failover 在模型服务。

唯一构造入口 ``for_invoke_ref(ref, pool=...)``（设计 §2.2），文本结果按原下标返回、
空白位为 ``None``（服务侧拒收空白条目，km 层剔除并按位回填 ``None``）。

结构化面（qwen3-vl 单向量融合）：``embed_contents`` / ``aembed_contents`` 收包
``EmbeddingRequest``（purpose + contents ≤20 块/图 ≤10），结果重建包 ``EmbeddingResult``
（dimension 由包 contract 强校验 2048）。``multimodal`` 构造旗由调用方按视图能力传入
（C 节站点判定），``is_multimodal_supported`` 原样透出——存量 ``_embed_chunks`` 依赖它
选择「逐文本 ``embed_batch``」路径。

与旧 ``RedBearEmbeddings`` 的差别是**不持有 api_key**：km 不再有 embedding 凭据解密面。
"""

from __future__ import annotations

from typing import Any

from langchain_core.embeddings import Embeddings
from redbear_model.contracts import EmbeddingRequest, EmbeddingResult

from .invoke_backend import (
    RemoteInvokeRef,
    acall_embedding,
    acall_embedding_contents,
    call_embedding_contents_sync,
    call_embedding_sync,
)
from .runtime import ModelInvokeRuntime


class RedBearEmbeddings(Embeddings):
    """统一的 Embedding 壳：构造入口 ``for_invoke_ref(ref, pool=...)``。"""

    @classmethod
    def for_invoke_ref(
        cls,
        ref: RemoteInvokeRef,
        *,
        pool: ModelInvokeRuntime,
        multimodal: bool = False,
    ) -> RedBearEmbeddings:
        """远端模式：km 不再持有凭据，调用只带配置 id 与租户。

        ``multimodal``：该配置是否走结构化单向量路径（qwen3-vl 视图判定），由调用方传入。
        """

        instance = cls.__new__(cls)
        instance._remote = ref
        instance._pool = pool
        instance._multimodal = multimodal
        return instance

    def _unsupported(self, feature: str) -> NotImplementedError:
        return NotImplementedError(f"{feature}在远端模式下不可用")

    # ==================== LangChain 标准接口 ====================

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化（LangChain 标准接口）；空白位返回 ``None``。"""

        return call_embedding_sync(self._pool, self._remote, texts)  # type: ignore[return-value]

    def embed_query(self, text: str) -> list[float]:
        """单个文本向量化（LangChain 标准接口）。"""

        result = call_embedding_sync(self._pool, self._remote, [text])
        return result[0] or []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化（异步）；空白位返回 ``None``。"""

        return await acall_embedding(self._pool, self._remote, texts)  # type: ignore[return-value]

    async def aembed_query(self, text: str) -> list[float]:
        """单个文本向量化（异步）。"""

        result = await acall_embedding(self._pool, self._remote, [text])
        return result[0] or []

    # ==================== 结构化面（qwen3-vl 单向量融合） ====================

    def embed_contents(self, request: EmbeddingRequest) -> EmbeddingResult:
        """结构化向量化（同步，ES 向量库链路）：单请求融合为单向量。"""

        return call_embedding_contents_sync(self._pool, self._remote, request)

    async def aembed_contents(self, request: EmbeddingRequest) -> EmbeddingResult:
        """结构化向量化（异步）。"""

        return await acall_embedding_contents(self._pool, self._remote, request)

    # ==================== 兼容面（存量消费方词表） ====================

    def embed_batch(self, items: list[Any], **kwargs: Any) -> list[list[float]]:
        """批量向量化：全字符串走标准方法（存量 ``_embed_chunks`` 逐文本调用）。"""

        if all(isinstance(item, str) for item in items):
            return self.embed_documents(items)
        raise self._unsupported("混合类型批量向量化")

    def is_multimodal_supported(self) -> bool:
        """该配置是否支持结构化（多模态）入参——由调用方视图判定后传入。"""

        return self._multimodal


__all__ = ["RedBearEmbeddings"]
