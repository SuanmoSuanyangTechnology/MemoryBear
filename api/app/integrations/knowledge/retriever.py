"""Semantic retrieval interface shared by local and remote adapters."""

from __future__ import annotations

from typing import Protocol

from app.schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)

from .contracts import KnowledgeCallContext


class KnowledgeRetriever(Protocol):
    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        context: KnowledgeCallContext,
    ) -> KnowledgeRetrievalResult:
        raise NotImplementedError
