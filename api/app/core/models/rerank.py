"""Rerank 壳（远端模式）：宿主只持有配置引用，凭据、选路与 failover 在模型服务。

运行面调用一律经 ``for_invoke(ref)`` 构造 → ``POST /internal/v1/invoke``（设计 §2.2），
结果下标为原文稿下标；多模态重排随 G4 接入服务侧。
"""

from typing import Any, Dict, List, Optional, Sequence, Union
from copy import deepcopy

from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document

from app.integrations.model.invoke_backend import (
    RemoteInvokeRef,
    acall_rerank,
    call_rerank_sync,
)


def _document_texts(documents: Sequence[Any]) -> List[str]:
    """屏蔽载体差异：Document 取正文，其余按字符串用（服务侧只认文本）。"""

    return [
        document.page_content if isinstance(document, Document) else str(document)
        for document in documents
    ]


class RedBearRerank(BaseDocumentCompressor):
    """ Rerank → 作为 Runnable 插入任意 LCEL 链

    唯一构造入口 ``for_invoke(ref)``（远端模式，结果下标为原文稿下标）；
    宿主不再持有 api_key / 模型实例。
    """

    @classmethod
    def for_invoke(cls, ref: RemoteInvokeRef) -> "RedBearRerank":
        """远端模式：宿主不再持有凭据（设计 §2.2），调用只带配置 id 与租户。"""
        instance = cls.model_construct()
        instance._remote = ref
        return instance

    def compress_documents(
            self,
            documents: Sequence[Document],
            query: str,
            callbacks: Optional[Callbacks] = None,
            *,
            top_n: Optional[int] = -1,
    ) -> Sequence[Document]:
        """
        重排并压缩文档（远端调用，结果下标为原文稿下标）。

        Args:
            documents: A sequence of documents to compress.
            query: The query to use for compressing the documents.
            callbacks: Callbacks to run during the compression process.
            top_n: Number of top documents to return after reranking.

        Returns:
            A sequence of compressed documents.
        """
        return self._compressed(documents, self.rerank(documents, query, top_n=top_n))

    async def acompress_documents(
            self,
            documents: Sequence[Document],
            query: str,
            callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        """异步压缩：走异步通道（签名与基类一致，基类无 ``top_n``，全量重排由调用方截取）。"""
        ranked = await acall_rerank(
            self._remote, query=query, documents=_document_texts(documents)
        )
        return self._compressed(documents, ranked)

    @staticmethod
    def _compressed(
            documents: Sequence[Document],
            ranked: Sequence[Dict[str, Any]],
    ) -> List[Document]:
        compressed = []
        for res in ranked:
            doc = documents[res["index"]]
            doc_copy = Document(doc.page_content, metadata=deepcopy(doc.metadata))
            doc_copy.metadata["relevance_score"] = res["relevance_score"]
            compressed.append(doc_copy)
        return compressed

    def rerank(
            self,
            documents: Sequence[Union[str, Document, dict]],
            query: str,
            *,
            top_n: Optional[int] = -1,
    ) -> List[Dict[str, Any]]:
        return call_rerank_sync(
            self._remote,
            query=query,
            documents=_document_texts(documents),
            top_n=top_n,
        )
