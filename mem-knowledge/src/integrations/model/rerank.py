"""Rerank 壳（远端模式，G4b）：km 只持有配置引用，凭据、选路与 failover 在模型服务。

唯一构造入口 ``for_invoke_ref(ref, pool=...)``（设计 §2.2），文本结果下标为原文稿下标
（空白稿件被剔除后映回）；多模态面 ``arerank_multimodal`` 走服务侧 qwen3-vl 原生多模态，
wire ``index`` 为 ``views[].chunk_index``、本层映回数组位置并构造包 ``RerankScore``。

与旧 ``RedBearRerank`` 的差别是**不持有 api_key**：km 不再有 rerank 凭据解密面。
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document
from redbear_model.contracts import (
    ImageEmbeddingContent,
    RerankCandidateView,
    RerankScore,
    TextEmbeddingContent,
)

from .invoke_backend import (
    RemoteInvokeRef,
    acall_rerank,
    acall_rerank_multimodal,
    call_rerank_sync,
)
from .runtime import ModelInvokeRuntime


def _document_texts(documents: Sequence[Any]) -> list[str]:
    """屏蔽载体差异：Document 取正文，其余按字符串用（服务侧只认文本）。"""

    return [
        document.page_content if isinstance(document, Document) else str(document)
        for document in documents
    ]


class RedBearRerank(BaseDocumentCompressor):
    """Rerank 压缩器：可作 Runnable 插入任意 LCEL 链。

    远端模式（结果下标为原文稿下标）；km 不再持有 api_key / 模型实例。
    """

    @classmethod
    def for_invoke_ref(
        cls, ref: RemoteInvokeRef, *, pool: ModelInvokeRuntime
    ) -> RedBearRerank:
        """远端模式：调用只带配置 id 与租户（设计 §2.2）。"""

        instance = cls.model_construct()
        instance._remote = ref
        instance._pool = pool
        return instance

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks | None = None,
        *,
        top_n: int | None = -1,
    ) -> Sequence[Document]:
        """重排并压缩文档（远端调用，结果下标为原文稿下标；``top_n<=0`` 为全部）。"""

        ranked = call_rerank_sync(
            self._pool,
            self._remote,
            query=query,
            documents=_document_texts(documents),
            top_n=top_n,
        )
        return self._compressed(documents, ranked)

    async def acompress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks | None = None,
    ) -> Sequence[Document]:
        """异步压缩：走异步通道（签名与基类一致，基类无 ``top_n``，全量重排）。"""

        ranked = await acall_rerank(
            self._pool, self._remote, query=query, documents=_document_texts(documents)
        )
        return self._compressed(documents, ranked)

    async def arerank_multimodal(
        self,
        query: TextEmbeddingContent | ImageEmbeddingContent,
        views: Sequence[RerankCandidateView],
        *,
        top_n: int | None = None,
    ) -> list[RerankScore]:
        """多模态重排（qwen3-vl）：``RerankScore.input_index`` 为 ``views`` 数组位置。"""

        return await acall_rerank_multimodal(
            self._pool, self._remote, query_content=query, views=views, top_n=top_n
        )

    @staticmethod
    def _compressed(
        documents: Sequence[Document],
        ranked: Sequence[dict[str, Any]],
    ) -> list[Document]:
        compressed = []
        for res in ranked:
            doc = documents[res["index"]]
            doc_copy = Document(doc.page_content, metadata=deepcopy(doc.metadata))
            doc_copy.metadata["relevance_score"] = res["relevance_score"]
            compressed.append(doc_copy)
        return compressed


__all__ = ["RedBearRerank"]
