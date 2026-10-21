"""Fail-closed stand-in used when remote knowledge integration is disabled.

The monolith serves knowledge retrieval in-process through its own controllers;
this service never owns that path, so the "disabled" mode only exists to keep
call sites importable. Any attempt to actually retrieve fails closed.
"""

from __future__ import annotations

from ...schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)

from .contracts import KnowledgeCallContext, KnowledgeConfigurationError


class LegacyKnowledgeRetriever:
    async def retrieval_policy(
        self,
        *,
        kb_ids: list[str],
        context: KnowledgeCallContext,
        rerank_id: str | None = None,
    ) -> dict[str, frozenset[str]]:
        raise KnowledgeConfigurationError(
            "In-process knowledge retrieval is not available in this service"
        )

    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        context: KnowledgeCallContext,
    ) -> KnowledgeRetrievalResult:
        raise KnowledgeConfigurationError(
            "In-process knowledge retrieval is not available in this service"
        )
