"""Memory-read domain vocabulary.

These values are wire-frozen: they mirror ``api/app/core/memory/enums.py`` and
``api/app/core/memory/storage/enums.py`` so a migrated response keeps the exact
strings the host has always emitted.
"""

from __future__ import annotations

from enum import StrEnum


class StorageType(StrEnum):
    """Where a workspace keeps its memory facts."""

    NEO4J = "neo4j"
    RAG = "rag"


class SearchStrategy(StrEnum):
    """Retrieval strategy, keyed by the public ``search_switch`` value.

    ``_missing_`` keeps the historical aliases ("deep", "deep", ...) working
    exactly like the host implementation: the request may carry either the
    numeric code or the lowercase name.
    """

    DEEP = "0"
    NORMAL = "1"
    QUICK = "2"
    EXPRESS = "5"
    COMMUNITY = "6"
    RECENT = "3"
    META = "4"

    @classmethod
    def _missing_(cls, value: object) -> SearchStrategy:
        aliases = {
            "deep": cls.DEEP,
            "normal": cls.NORMAL,
            "quick": cls.QUICK,
            "recent": cls.RECENT,
            "meta": cls.META,
            "express": cls.EXPRESS,
            "community": cls.COMMUNITY,
            "conv": cls.RECENT,
        }
        member = aliases.get(str(value).lower())
        if member is None:
            raise ValueError(f"Unknown search strategy {value!r}")
        return member


class MemorySource(StrEnum):
    """Graph/RAG node family a retrieved memory item came from.

    Values match ``MemoryNodeType`` on the host so a serialized ``source`` field
    is byte-identical after migration.
    """

    ASSISTANT_ORIGINAL = "AssistantOriginal"
    ASSISTANT_PRUNED = "AssistantPruned"
    CHUNK = "Chunk"
    COMMUNITY = "Community"
    CONVERSATION = "Conversation"
    DIALOGUE = "Dialogue"
    EXTRACTED_ENTITY = "ExtractedEntity"
    MEMORY_SUMMARY = "MemorySummary"
    PERCEPTUAL = "Perceptual"
    PREFERENCE = "Preference"
    #: Public source value of a RAG retrieval hit (``Neo4jNodeType.RAG``).
    RAG = "Rag"
    #: Storage-level label of the RAG memory node (``MemoryNodeType.RAG_MEMORY``).
    RAG_MEMORY = "RagMemory"
    SCENE_COMMUNITY = "SceneCommunity"
    SCENE_SUMMARY = "SceneSummary"
    STATEMENT = "Statement"
    USER_SOURCE = "UserSource"
    #: Pseudo sources used by the conversation-history and user-metadata reads.
    HISTORY = "HISTORY"


class MemoryDisplayType(StrEnum):
    """Stable public memory classification used by display projections."""

    PROFILE = "profile"
    FACT = "fact"
    ENTITY = "entity"
    SUMMARY = "summary"
    FILE = "file"
    UNKNOWN = "unknown"


class BranchStatus(StrEnum):
    """Status of one retrieval branch inside an execution trace."""

    COMPLETED = "completed"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"


__all__ = [
    "BranchStatus",
    "MemoryDisplayType",
    "MemorySource",
    "SearchStrategy",
    "StorageType",
]