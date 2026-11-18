"""Ports (Protocols) the memory-read core depends on.

The application layer only talks to these interfaces; ``src.core.adapters``
supplies the implementations and ``src.core.composition`` injects them. Keeping
the contracts here is what makes the use cases unit-testable without a database,
a Neo4j driver, Redis or the model service.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from src.core.domain.errors import MemoryRetrievalBusinessError
from src.core.domain.models import (
    EndUserRef,
    MemoryItem,
    ReadConfig,
    ReadContext,
    ReadResult,
    RetrievalExecutionTrace,
)

# ────────────────────────────────────────────────────────────────
# Identity / configuration
# ────────────────────────────────────────────────────────────────


class EndUserPort(Protocol):
    """Resolve an end user inside the caller's workspace (merge-aware)."""

    async def resolve_in_workspace(
        self, end_user_id: str, workspace_id: UUID
    ) -> EndUserRef | None:
        """Return the canonical (merge-routed) id, or ``None`` when unusable."""
        ...


class ReadConfigPort(Protocol):
    """The PostgreSQL facts a read needs (memory config, workspace, reembed state)."""

    async def active_config_id(self, workspace_id: UUID) -> UUID | None:
        """The workspace's active memory configuration (``workspaces.memory_config``)."""
        ...

    async def load_config(self, config_id: UUID) -> ReadConfig | None:
        """Load the configuration and its workspace's effective model slots."""
        ...

    async def workspace_storage_type(self, workspace_id: UUID) -> str | None:
        """``neo4j`` / ``rag``; ``None`` means "unknown, caller uses default"."""
        ...

    async def semantic_rebuild_pending(
        self,
        *,
        workspace_id: UUID,
        end_user_id: str,
        embedding_model_id: UUID,
    ) -> bool:
        """Whether stored vectors are still being rebuilt for the current model."""
        ...


# ────────────────────────────────────────────────────────────────
# Model clients (credentials and channel selection live in model-service)
# ────────────────────────────────────────────────────────────────


class ChatClient(Protocol):
    """The chat-model surface the retrieval and answer code uses."""

    async def ainvoke(
        self, messages: Any, config: Mapping[str, Any] | None = None
    ) -> Any: ...

    def bind_tools(self, tools: Sequence[Any]) -> ChatClient: ...

    async def call_structured(self, payload: Any, schema: Any, **kwargs: Any) -> Any:
        """Structured output with the host's repaired-JSON fallback."""
        ...


class EmbeddingClient(Protocol):
    async def aembed_query(self, text: str) -> list[float]: ...


class RerankDocument(Protocol):
    """One reranked document: langchain-compatible ``page_content``/``metadata``."""

    page_content: str
    metadata: dict[str, Any]


class RerankClient(Protocol):
    async def compress_documents(
        self,
        documents: Sequence[Any],
        query: str,
        *,
        top_n: int | None = None,
    ) -> list[RerankDocument]:
        """Score documents against the query; order and metadata as the host does."""
        ...


class ModelGateway(Protocol):
    """Resolve a model slot of the read context into a callable client."""

    async def chat(self, ctx: ReadContext) -> ChatClient: ...

    async def embedding(self, ctx: ReadContext) -> EmbeddingClient | None:
        """``None`` when the embedding slot is not configured (semantic degrade)."""
        ...

    async def rerank(self, ctx: ReadContext) -> RerankClient | None:
        """``None`` when the rerank slot is not configured (score fusion)."""
        ...

    async def perceptual_chat(self, ctx: ReadContext, perceptual_type: int) -> ChatClient | None:
        """Vision/audio slots use the chat family; ``None`` when not configured."""
        ...

    async def perceptual_model_view(
        self, ctx: ReadContext, perceptual_type: int
    ) -> Mapping[str, Any] | None:
        """Non-decrypted model view (provider / modality gates only)."""
        ...

    async def model_name(self, model_id: UUID, tenant_id: UUID) -> str:
        """Display name of a model configuration (community trace only)."""
        ...

    async def multimodal_formatter(
        self, ctx: ReadContext, model_view: Mapping[str, Any]
    ) -> MultimodalFormatter:
        """Formatter that turns file references into chat content blocks."""
        ...


class MultimodalFormatter(Protocol):
    async def format_files(
        self,
        files: Sequence[Mapping[str, Any]],
        *,
        document_image_recognition: bool,
        include_processing_errors: bool,
    ) -> list[dict[str, Any]]:
        """Return provider-native content blocks for the given file inputs."""
        ...


# ────────────────────────────────────────────────────────────────
# Retrieval
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CandidateItem:
    """One storage-level candidate before the read pipeline scores it."""

    label: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    """Bounded candidate pool returned by one storage recall call."""

    items: Sequence[CandidateItem] = field(default_factory=tuple)
    backend: str = "neo4j"

    @property
    def total(self) -> int:
        """The provider exposes a bounded pool, not an exact match total."""
        return len(self.items)


@dataclass(frozen=True, slots=True)
class CommunityRecallQuery:
    """Domain criteria for the community recall port.

    The caller states *what* community context it wants (whose memory, which
    community); the adapter owns how that maps onto storage labels, status
    values and physical properties (``end_user_id`` / ``delete_at`` / ...).
    """

    end_user_id: str
    community_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReadSearchDeps:
    """Model clients a search session was built with (one set per request)."""

    embedder: EmbeddingClient | None = None
    llm: ChatClient | None = None
    reranker: RerankClient | None = None
    semantic_degraded: bool = False


class ReadSearchSession(Protocol):
    """One retrieval session bound to a read context and its model clients.

    Signatures mirror the host's ``Neo4jSearchService`` / ``RAGSearchService`` so
    the ported pipeline keeps identical branching and merge semantics.
    """

    async def keyword_search(
        self,
        query: str,
        limit: int = 10,
        *,
        entity_limit: int | None = None,
        apply_source_dedup: bool = False,
    ) -> ReadResult: ...

    async def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        *,
        entity_limit: int | None = None,
        apply_source_dedup: bool = False,
    ) -> ReadResult: ...

    async def relation_search(self, query: str) -> ReadResult: ...

    async def recall_candidates(
        self,
        query: CommunityRecallQuery,
        value: str | list[float],
        *,
        limit: int,
    ) -> CandidateBatch:
        """Community recall override: raw storage candidates for community nodes.

        The adapter resolves the label, the scope filters and any storage-side
        status conventions from ``query``; the caller only states the domain
        context.
        """
        ...

    async def resolve_perceptual_content(
        self,
        query: str,
        memories: list[MemoryItem],
        llm: ChatClient,
        model_view: Mapping[str, Any] | None,
    ) -> list[MemoryItem]: ...

    async def user_metadata(self) -> MemoryItem | None:
        """The user-profile (memory L0) item, or ``None`` when the storage has none.

        RAG workspaces have no graph profile (host parity), so the pipeline must
        be able to observe its absence instead of receiving a placeholder.
        """
        ...

    async def conversation_history(self) -> ReadResult:
        """The RECENT strategy: recent dialogue turns from PostgreSQL."""
        ...


class ReadSearchFactory(Protocol):
    """Build a search session for one read context."""

    def create(
        self,
        ctx: ReadContext,
        *,
        includes: tuple[str, ...] | None,
        deps: ReadSearchDeps,
        on_error: Callable[[MemoryRetrievalBusinessError], None],
        cosine_score_threshold: float | None = None,
    ) -> ReadSearchSession: ...


# ────────────────────────────────────────────────────────────────
# Prompts / session history / side effects / alerting
# ────────────────────────────────────────────────────────────────


class PromptRenderer(Protocol):
    """Render one versioned Jinja2 prompt resource."""

    def render(self, name: str, **context: Any) -> str: ...


class SessionHistoryPort(Protocol):
    """Conversation history for the streaming (validation) read."""

    async def history(self, session_id: str) -> list[dict[str, str]]: ...

    async def append(self, session_id: str, messages: Sequence[Mapping[str, str]]) -> None: ...


class ReadSideEffectsPort(Protocol):
    """Non-fatal effects of a read (short-term memo and retrieval display card)."""

    async def save_short_term(
        self,
        *,
        end_user_id: str,
        query: str,
        answer: str,
        memories: Sequence[MemoryItem],
        search_switch: str,
    ) -> None: ...

    async def enqueue_display(
        self,
        *,
        end_user_id: str,
        workspace_id: UUID | None,
        language: str,
        search_mode: str,
        query: str,
        result: ReadResult,
    ) -> bool: ...


class RetrievalAlertPort(Protocol):
    """Optional enterprise notification bridge for degraded retrieval branches."""

    async def notify(
        self,
        error: MemoryRetrievalBusinessError,
        *,
        operation_id: str,
        tenant_id: str,
        workspace_id: str,
        end_user_id: str,
    ) -> None: ...


class AnswerGenerator(Protocol):
    """Final answer generation over the retrieval result."""

    async def summarize(
        self, *, ctx: ReadContext, query: str, result: ReadResult
    ) -> str: ...

    async def community_answer(
        self, *, ctx: ReadContext, query: str, evidence: Sequence[Mapping[str, Any]]
    ) -> str: ...


__all__ = [
    "AnswerGenerator",
    "CandidateBatch",
    "CandidateItem",
    "ChatClient",
    "CommunityRecallQuery",
    "EmbeddingClient",
    "EndUserPort",
    "ModelGateway",
    "MultimodalFormatter",
    "PromptRenderer",
    "ReadConfigPort",
    "ReadSearchDeps",
    "ReadSearchFactory",
    "ReadSearchSession",
    "ReadSideEffectsPort",
    "RerankClient",
    "RerankDocument",
    "RetrievalAlertPort",
    "RetrievalExecutionTrace",
    "SessionHistoryPort",
]