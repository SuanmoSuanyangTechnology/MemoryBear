"""Publish compatible knowledge task messages without registering task bodies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from ..errors import KnowledgeError
from .celery_app import KNOWLEDGE_TASK_ROUTES, celery_app

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
    ) -> str:
        expected_queue = KNOWLEDGE_TASK_ROUTES.get(name)
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
            result = await asyncio.to_thread(
                self._application.send_task,
                name,
                args=list(args or ()),
                kwargs=dict(kwargs or {}),
                queue=expected_queue,
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
