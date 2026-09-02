"""Adapter from the retrieval interface to the original API implementation."""

from __future__ import annotations

from app.core.rag.retrieval.models import RetrievalPrincipal
from app.schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)
from app.schemas.rerank_schema import RerankMode
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService

from .contracts import (
    KnowledgeCallContext,
    KnowledgeContextError,
    KnowledgeRetrievalSource,
)
from .errors import KnowledgeServiceError


class LegacyKnowledgeRetriever:
    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        context: KnowledgeCallContext,
    ) -> KnowledgeRetrievalResult:
        if context.principal is None:
            raise KnowledgeContextError("Knowledge retrieval principal is required")
        explicit_mode = request.rerank_mode is not None or any(
            config.rerank_mode is not None
            for config in request.knowledge_bases
        )
        if context.source is KnowledgeRetrievalSource.EXTERNAL_API and explicit_mode:
            raise KnowledgeServiceError(
                400,
                400,
                "Explicit rerank modes are not available to API Key callers",
                context.trace_id,
            )

        explicit_weighted = request.rerank_mode is RerankMode.WEIGHTED_SCORE or any(
            config.rerank_mode is RerankMode.WEIGHTED_SCORE
            for config in request.knowledge_bases
        )
        if explicit_weighted:
            raise KnowledgeServiceError(
                400,
                400,
                "weighted_score requires the independent knowledge service",
                context.trace_id,
            )
        normalized = request.model_copy(update={"source": context.source})
        principal = RetrievalPrincipal(
            id=context.principal.actor_id,
            username=context.principal.actor_name,
            tenant_id=context.principal.tenant_id,
            current_workspace_id=context.principal.workspace_id,
            is_superuser=False,
        )
        return await KnowledgeRetrievalService.retrieve_async(normalized, principal)
