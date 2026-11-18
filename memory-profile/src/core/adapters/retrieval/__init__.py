"""Graph / Elasticsearch retrieval adapter.

Implements the memory-read retrieval ports (``ReadSearchFactory`` /
``ReadSearchSession``) on top of the storage substrate
(:mod:`src.core.adapters.storage`):

* the storage DSL (filters, projections, index registry) and the Neo4j /
  Elasticsearch read clients live in ``adapters.storage``;
* this package contains the retrieval sessions themselves — content search,
  quick retrieval, relation agent, metadata and result builders.

No module in this package imports ``api``/``app`` or the application layer.
"""

from __future__ import annotations

from src.core.adapters.retrieval.factory import (
    RetrievalSearchFactory,
    build_read_storage,
    build_search_factory,
)
from src.core.adapters.retrieval.search import Neo4jSearchSession, RagSearchSession

__all__ = [
    "Neo4jSearchSession",
    "RagSearchSession",
    "RetrievalSearchFactory",
    "build_read_storage",
    "build_search_factory",
]