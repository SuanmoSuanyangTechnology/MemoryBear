"""Task-scoped context propagated to log records."""

from __future__ import annotations

from contextvars import ContextVar, Token

_TASK_ID: ContextVar[str] = ContextVar("memory_jobs_task_id", default="")


def get_task_id() -> str:
    return _TASK_ID.get()


def set_task_id(task_id: str | None) -> Token[str]:
    return _TASK_ID.set(task_id or "")


def reset_task_id(token: Token[str]) -> None:
    _TASK_ID.reset(token)


def clear_task_id() -> None:
    _TASK_ID.set("")
