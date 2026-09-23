"""Semantic retrieval interface shared by local and remote adapters."""

from __future__ import annotations

from typing import Protocol

from app.schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)

from .contracts import KnowledgeCallContext


class KnowledgeRetriever(Protocol):
    async def retrieval_policy(
        self,
        *,
        kb_ids: list[str],
        context: KnowledgeCallContext,
        rerank_id: str | None = None,
    ) -> dict[str, frozenset[str]]:
        """Return the query modalities supported by each retrieval mode."""
        raise NotImplementedError

    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        context: KnowledgeCallContext,
    ) -> KnowledgeRetrievalResult:
        raise NotImplementedError
