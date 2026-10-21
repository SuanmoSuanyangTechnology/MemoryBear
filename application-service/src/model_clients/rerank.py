"""Rerank shell (remote mode): the host only holds a config reference; credentials, routing and failover live in the model service.

Runtime calls are always constructed via ``for_invoke(ref)`` -> ``POST /internal/v1/invoke``;
result indices refer to the original documents; multimodal rerank is not yet served by the
model service.
"""

from typing import Any, Dict, List, Optional, Sequence, Union
from copy import deepcopy

from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document

from ..integrations.model.invoke_backend import (
    RemoteInvokeRef,
    acall_rerank,
    call_rerank_sync,
)


def _document_texts(documents: Sequence[Any]) -> List[str]:
    """Hide carrier differences: Documents yield page_content, everything else is stringified (the service side accepts text only)."""

    return [
        document.page_content if isinstance(document, Document) else str(document)
        for document in documents
    ]


class RedBearRerank(BaseDocumentCompressor):
    """Rerank wrapper -> usable as a Runnable in any LCEL chain

    The sole construction entry is ``for_invoke(ref)`` (remote mode; result indices refer
    to the original documents); the host no longer holds an api_key / model instance.
    """

    @classmethod
    def for_invoke(cls, ref: RemoteInvokeRef) -> "RedBearRerank":
        """Remote mode: the host no longer holds credentials; calls carry only the config id and tenant."""
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
        Rerank and compress documents (remote call; result indices refer to the original documents).

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
        """Async compression over the async channel (signature matches the base class, which has no ``top_n``; the caller truncates the full rerank result)."""
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
