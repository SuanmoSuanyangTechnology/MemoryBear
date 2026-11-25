"""Application commands and results for this task context."""

from dataclasses import dataclass
from typing import Any

from src.application.ports.task_context import TaskScope


@dataclass(frozen=True, slots=True)
class TriggerForgettingCommand:
    scope: TaskScope
    attempt_token: str
    max_merge_batch_size: int = 100
    min_days_since_access: int = 30
    language: str = "zh"


@dataclass(frozen=True, slots=True)
class ForgettingResult:
    data: dict[str, Any]
