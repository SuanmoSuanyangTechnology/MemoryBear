"""Stream result dataclass for async batch message persistence.

``StreamResult`` accumulates streaming output in memory during LLM inference,
then ``BatchPersistQueue`` writes it to the database asynchronously in batches.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Stream result (mutable accumulator for async batch persist)
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
