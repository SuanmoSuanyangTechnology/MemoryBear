"""Publish compatible knowledge task messages without registering task bodies."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

from ..bootstrap import get_settings
from ..errors import KnowledgeError
from ..trace import get_trace_id
from .celery_app import PUBLISHABLE_KNOWLEDGE_TASK_ROUTES, celery_app
from .observability import current_parent_task_id

logger = logging.getLogger(__name__)


class TaskDispatcher:
    """Validate and publish messages for the existing knowledge workers."""

    def __init__(self, application: Any = celery_app):
        self._application = application

    async def send(
        self,
        name: str,
        *,
        args: Sequence[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        queue: str | None = None,
        task_id: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self.send_sync,
            name,
            args=args,
            kwargs=kwargs,
            queue=queue,
            task_id=task_id,
        )

    def send_sync(
        self,
        name: str,
        *,
        args: Sequence[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        queue: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Validate and publish a task from a synchronous execution context."""

        expected_queue = PUBLISHABLE_KNOWLEDGE_TASK_ROUTES.get(name)
        if expected_queue is None:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                f"Unknown knowledge task: {name}",
            )
        if queue is not None and queue != expected_queue:
            raise KnowledgeError.from_code(
                "KB_VALIDATION_ERROR",
                f"Invalid queue for knowledge task: {name}",
            )
        try:
            send_task_kwargs: dict[str, Any] = {
                "args": list(args or ()),
                "kwargs": dict(kwargs or {}),
                "queue": expected_queue,
                "headers": {
                    key: value
                    for key, value in {
                        "kb_published_at_ms": int(time.time() * 1000),
                        "kb_trace_id": get_trace_id() or None,
                        "kb_parent_task_id": current_parent_task_id(),
                        "kb_source_role": get_settings().kb_process_role,
                    }.items()
                    if value is not None
                },
            }
            if task_id:
                send_task_kwargs["task_id"] = task_id
            result = self._application.send_task(
                name,
                **send_task_kwargs,
            )
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeError.from_code(
                "KB_TASK_DISPATCH_FAILED",
                f"Failed to dispatch knowledge task: {name}",
            ) from exc
        task_id = getattr(result, "id", None)
        if not task_id:
            raise KnowledgeError.from_code(
                "KB_TASK_DISPATCH_FAILED",
                f"Knowledge task did not return an id: {name}",
            )
        return str(task_id)

    async def revoke(self, task_id: str) -> bool:
        """Best-effort revoke compatible with the legacy delete path."""

        try:
            await asyncio.to_thread(self._application.control.revoke, task_id)
            return True
        except NotImplementedError:
            return False
        except Exception:
            logger.warning("Failed to revoke knowledge task: task_id=%s", task_id)
            return False


__all__ = ["TaskDispatcher"]
