"""Adapter from the retrieval interface to the original API implementation."""

from __future__ import annotations

from app.core.rag.retrieval.models import RetrievalPrincipal
from app.schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService

from .contracts import KnowledgeCallContext, KnowledgeContextError


class LegacyKnowledgeRetriever:
    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        context: KnowledgeCallContext,
    ) -> KnowledgeRetrievalResult:
        if context.principal is None:
            raise KnowledgeContextError("Knowledge retrieval principal is required")
        normalized = request.model_copy(update={"source": context.source})
        principal = RetrievalPrincipal(
            id=context.principal.actor_id,
            username=context.principal.actor_name,
            tenant_id=context.principal.tenant_id,
            current_workspace_id=context.principal.workspace_id,
            is_superuser=False,
        )
        return await KnowledgeRetrievalService.retrieve_async(normalized, principal)
