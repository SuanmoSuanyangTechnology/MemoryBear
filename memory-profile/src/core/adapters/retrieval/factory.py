"""Build the retrieval search factory from the process runtime.

The composition root calls :func:`build_search_factory` once per process; the
returned factory creates one stateless session per read context. No client is
opened here: the infrastructure managers own their lifecycle and connect lazily.

Storage access and the read DSL live in
:mod:`src.core.adapters.storage`; this module only composes them into search
sessions. ``build_read_storage`` is re-exported for callers that already treat
the retrieval adapter as their wiring point (kept stable for tests and the
composition root).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from src.core.adapters.retrieval.queries import RetrievalQueries
from src.core.adapters.retrieval.search import Neo4jSearchSession, RagSearchSession
from src.core.adapters.storage import (
    DEFAULT_READ_BACKEND,
    ReadStorage,
    build_read_storage,
)
from src.core.domain.enums import StorageType
from src.core.domain.errors import MemoryRetrievalBusinessError
from src.core.domain.models import ReadContext
from src.core.domain.ports import (
    ModelGateway,
    PromptRenderer,
    ReadSearchDeps,
    ReadSearchSession,
)

logger = logging.getLogger(__name__)


class RetrievalSearchFactory:
    """Create one :class:`ReadSearchSession` per read context."""

    def __init__(
        self,
        *,
        storage: ReadStorage,
        queries: RetrievalQueries,
        prompts: PromptRenderer,
        models: ModelGateway,
    ) -> None:
        self._storage = storage
        self._queries = queries
        self._prompts = prompts
        self._models = models

    def create(
        self,
        ctx: ReadContext,
        *,
        includes: Sequence[str] | None,
        deps: ReadSearchDeps,
        on_error: Callable[[MemoryRetrievalBusinessError], None],
        cosine_score_threshold: float | None = None,
    ) -> ReadSearchSession:
        if _storage_type(ctx) == StorageType.RAG:
            return RagSearchSession(
                ctx=ctx,
                deps=deps,
                includes=includes,
                on_error=on_error,
                storage=self._storage,
                queries=self._queries,
                prompts=self._prompts,
                models=self._models,
                cosine_score_threshold=cosine_score_threshold,
            )
        return Neo4jSearchSession(
            ctx=ctx,
            deps=deps,
            includes=includes,
            on_error=on_error,
            storage=self._storage,
            queries=self._queries,
            prompts=self._prompts,
            models=self._models,
            cosine_score_threshold=cosine_score_threshold,
        )


def _storage_type(ctx: ReadContext) -> StorageType:
    storage_type = ctx.storage_type
    if isinstance(storage_type, StorageType):
        return storage_type
    try:
        return StorageType(str(storage_type))
    except ValueError:
        logger.warning(
            "[Retrieval] unknown storage type %r; defaulting to neo4j", storage_type
        )
        return StorageType.NEO4J


def build_search_factory(
    *,
    storage: ReadStorage,
    postgres: Any,
    prompts: PromptRenderer,
    models: ModelGateway,
    enterprise: Any | None = None,
) -> RetrievalSearchFactory:
    """Compose the read-search factory over an already-built storage adapter.

    The composition root constructs :class:`ReadStorage` (the adapter) and the
    PostgreSQL manager; this factory only wires the retrieval sessions on top of
    them. Keeping the construction outside this module makes the composition root
    the single place where adapters are instantiated.
    """

    logger.info("[Retrieval] read backend resolved to %s", storage.read_backend)
    return RetrievalSearchFactory(
        storage=storage,
        queries=RetrievalQueries(
            storage=storage, postgres=postgres, enterprise=enterprise
        ),
        prompts=prompts,
        models=models,
    )


__all__ = [
    "DEFAULT_READ_BACKEND",
    "RetrievalSearchFactory",
    "build_read_storage",
    "build_search_factory",
]