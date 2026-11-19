"""Application commands and results for this task context."""

from dataclasses import dataclass
from typing import Any

from src.application.ports.task_context import TaskScope


@dataclass(frozen=True, slots=True)
class GenerateEmotionSuggestionsCommand:
    scope: TaskScope
    language: str = "zh"


@dataclass(frozen=True, slots=True)
class EmotionSuggestionsResult:
    data: dict[str, Any]
