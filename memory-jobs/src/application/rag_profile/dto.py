"""Application commands and results for this task context."""

from dataclasses import dataclass
from typing import Any

from src.application.ports.task_context import TaskScope


@dataclass(frozen=True, slots=True)
class GenerateRAGProfileCommand:
    scope: TaskScope
    limit: int = 15
    max_tags: int = 10
    language: str = "zh"


@dataclass(frozen=True, slots=True)
class RAGProfileResult:
    data: dict[str, Any]
