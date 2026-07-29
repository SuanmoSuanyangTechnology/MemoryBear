"""Phase 1 load context and Phase 2 stream result dataclasses.

Three-phase chat streaming:
  Phase 1 (Load):  single short async session → ChatLoadContext (ORM-detached, frozen)
  Phase 2 (Stream): no DB connection, reads from ChatLoadContext, writes to StreamResult
  Phase 3 (Persist): single short async session, consumes StreamResult → DB writes
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Small value objects — detached from ORM before session closes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApiKeySnapshot:
    """ORM-detached snapshot of a ModelApiKey row."""
    id: uuid.UUID | None
    model_name: str
    provider: str
    api_key: str
    api_base: str
    capability: list[str]
    is_omni: bool


# ---------------------------------------------------------------------------
# Phase 1 result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChatLoadContext:
    """Immutable snapshot of everything loaded in Phase 1.

    All fields are plain Python values — zero ORM references.
    The session that produced this data is already closed when this is handed to Phase 2.
    """

    # -- identity -----------------------------------------------------------------
    conversation_id: uuid.UUID
    workspace_id: uuid.UUID
    app_id: uuid.UUID
    user_id: str  # end_user_id as str

    # -- model / api key ----------------------------------------------------------
    tenant_id: uuid.UUID | None
    api_key: ApiKeySnapshot

    # -- prompt & parameters ------------------------------------------------------
    system_prompt: str
    model_parameters: dict[str, Any]
    features_config: dict[str, Any]

    # -- tools / skills / knowledge / memory --------------------------------------
    tools: list[Any] = field(default_factory=list)
    skill_prompts: str = ""
    citations_collector: list[Any] = field(default_factory=list)
    memory_enabled: bool = False

    # -- history ------------------------------------------------------------------
    history: list[dict[str, Any]] | None = None
    is_new_conversation: bool = False
    opening_statement: str | None = None
    opening_suggested_questions: list[str] = field(default_factory=list)

    # -- files --------------------------------------------------------------------
    processed_files: list[Any] | None = None

    # -- memory storage -----------------------------------------------------------
    storage_type: str | None = None
    user_rag_memory_id: str | None = None

    # -- annotation fast-path -----------------------------------------------------
    annotation_match: dict[str, Any] | None = None

    # -- misc ---------------------------------------------------------------------
    source: str = ""

    @property
    def api_key_id(self) -> uuid.UUID | None:
        return self.api_key.id

    @property
    def api_key_model_name(self) -> str:
        return self.api_key.model_name

    @property
    def api_key_provider(self) -> str:
        return self.api_key.provider

    @property
    def api_key_is_omni(self) -> bool:
        return self.api_key.is_omni


# ---------------------------------------------------------------------------
# Phase 2 result (mutable accumulator)
# ---------------------------------------------------------------------------

@dataclass
class StreamResult:
    """Mutable accumulator populated during Phase 2 streaming."""

    message_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_message_id: uuid.UUID = field(default_factory=uuid.uuid4)
    full_content: str = ""
    full_reasoning: str = ""
    total_tokens: int = 0
    node_executions: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    execution_id: Optional[str] = None
    assistant_meta: Optional[dict[str, Any]] = None

    # Persist-time metadata populated during Phase 2 streaming
    suggested_questions: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    audio_url: Optional[str] = None
    audio_status: Optional[str] = None
    files_meta: list[dict[str, Any]] = field(default_factory=list)
    history_files: Optional[dict[str, Any]] = None

    @property
    def elapsed_time(self) -> float:
        return time.time() - self.start_time
