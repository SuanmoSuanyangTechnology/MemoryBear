"""Application commands and results for this task context."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.application.ports.task_context import TaskScope


@dataclass(frozen=True, slots=True)
class GenerateConversationDetailCommand:
    scope: TaskScope
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class ConversationDetailResult:
    data: dict[str, Any]
