"""Interest-distribution orchestration for the Celery task.

Resolve the workspace LLM, run the generation under its own per-call budget, and
degrade to an empty cacheable result instead of failing the caller's request.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.common.normalization import normalize_language
from src.infrastructure.model_service import ModelServiceManager
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.modules.interest_distribution.generation import (
    InterestDistributionGeneration,
    generate_interest_distribution,
)
from src.modules.shared.model_config import build_chat_model, resolve_chat_config
from src.modules.shared.repositories.generation_graph import GenerationGraphRepository
from src.modules.shared.repositories.model_config import ModelConfigRepository
from src.modules.shared.repositories.workspace import WorkspaceRepository

logger = logging.getLogger(__name__)


class InterestDistributionService:
    """Generate one user's interest distribution for the caller to cache."""

    def __init__(
        self,
        postgres: PostgresManager,
        neo4j: Neo4jManager,
        model_service: ModelServiceManager,
    ) -> None:
        self.workspace = WorkspaceRepository(postgres)
        self.graph = GenerationGraphRepository(neo4j)
        self.model_config = ModelConfigRepository(postgres)
        self.model_service = model_service

    def generate(
        self,
        workspace_id: UUID,
        end_user_id: str,
        language: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Generate the user's interest distribution for the caller to cache.

        The cache itself lives with the caller (memory-profile), which owns the
        key shape, TTL and the "cache the empty result too" rule; this method
        only reports whether the result is cacheable.
        """

        language = normalize_language(language)
        workspace_row = self.workspace.workspace_row(workspace_id)
        if workspace_row is None:
            return {"items": [], "cacheable": False}

        try:
            resolved = resolve_chat_config(self.model_config, workspace_row)
            generation = generate_interest_distribution(
                self.graph,
                lambda timeout_seconds: build_chat_model(
                    self.model_service, resolved, timeout_seconds=timeout_seconds
                ),
                end_user_id,
                limit=limit,
                language=language,
            )
        except Exception as exc:
            logger.error(
                "interest distribution degraded to empty: end_user_id=%s error=%s",
                end_user_id,
                type(exc).__name__,
                exc_info=True,
            )
            generation = InterestDistributionGeneration(items=[], cacheable=True)

        return {
            "items": [
                {"name": name, "frequency": frequency}
                for name, frequency in generation.items
            ],
            "cacheable": generation.cacheable,
        }
