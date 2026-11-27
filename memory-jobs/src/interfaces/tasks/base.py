"""Base task class: retry, acknowledgement and admission-completion policy.

Tasks reach infrastructure only through their use cases (composition root); this
module carries no client accessors, so a task cannot open a session or a driver
outside the Unit of Work.

The base class also owns the *completion* side of the dispatch-admission layer:
a work task published by the admission dispatcher carries the admitted message
id as an extra kwarg, and when it returns this is the only place that knows the
task finished. It publishes a dispatch signal so the admission worker can finish
the attempt and advance the unit.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from celery import Task, current_app
from sqlalchemy.exc import OperationalError
from task_bridge.mem_router import QueueName, TaskName, TaskPriority

from src.application.ports.admission.admission import ADMISSION_MSG_ID_KEY

logger = logging.getLogger(__name__)

#: Celery states that mean the work task will not run again. ``RETRY`` and
#: ``STARTED`` deliberately do not advance the admission layer.
_TERMINAL_STATES = frozenset({"SUCCESS", "FAILURE", "REVOKED"})

#: Longest result JSON forwarded with a completion. The tracker keeps a summary;
#: the result backend remains the source of truth for the full value.
MAX_COMPLETION_RESULT_LENGTH = 4_000
#: ``WRITE_INGEST`` is different: its result *is* the producer's response
#: (effective end-user id and the admitted task ids), so the waiting HTTP caller
#: needs the whole value. A bounded but generous budget keeps a large batch from
#: being truncated into a string the producer cannot parse.
INGEST_COMPLETION_RESULT_LENGTH = 256_000
#: Longest error text forwarded with a completion.
MAX_COMPLETION_ERROR_LENGTH = 2_000


def _json_safe(value: Any, *, max_length: int = MAX_COMPLETION_RESULT_LENGTH) -> Any:
    """Reduce a return value to something the completion kwargs can carry.

    Celery's JSON serializer may pass JSON-native values through, but an eager
    run or a custom serializer can hand back arbitrary objects. The completion
    is diagnostic, so normalizing and truncating is preferable to failing the
    publish after the work already succeeded.
    """

    try:
        encoded = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
    if len(encoded) > max_length:
        return encoded[:max_length]
    return json.loads(encoded)


def _error_text(einfo: Any, retval: Any, *, status: str) -> str:
    """The failure text forwarded with a completion.

    Celery 5.6 calls ``after_return`` with ``einfo=None`` on the worker path and
    passes the failure's exception as ``retval`` instead, so the traceback is
    only available from ``einfo`` on older/other executors. Fall back to the
    exception's text so a failed unit is diagnosable in the tracker either way.
    """

    text = ""
    if einfo is not None:
        try:
            text = str(einfo)
        except Exception:
            text = ""
    if not text and status != "SUCCESS" and retval is not None:
        try:
            text = str(retval)
        except Exception:
            text = ""
    return text[:MAX_COMPLETION_ERROR_LENGTH]


class JobTask(Task):
    """Base class for memory-jobs tasks.

    Retry policy is transport-only: ``OperationalError`` (connection lost,
    server restarting) is safe to replay, while business errors are not — a
    task must decide those itself. ``acks_late`` pairs with this: the message
    is acknowledged after the task returns, so a crashed worker redelivers it.
    Tasks must therefore be idempotent or guard their own side effects.
    """

    abstract = True

    autoretry_for = (OperationalError,)
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 5
    acks_late = True

    def after_return(
        self,
        status: str,
        retval: Any,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """Tell the admission layer that an admitted work task finished.

        Only a task published by the admission dispatcher carries
        :data:`ADMISSION_MSG_ID_KEY`; every other task returns here without
        doing anything. The publish is best effort: a broker hiccup must never
        turn a completed unit into a failed task (which ``acks_late`` would
        replay). The recovery sweep finalizes an attempt whose completion was
        lost, from the result backend.
        """

        if status not in _TERMINAL_STATES:
            return
        if not isinstance(kwargs, dict):
            return
        msg_id = kwargs.get(ADMISSION_MSG_ID_KEY)
        if not isinstance(msg_id, str) or not msg_id:
            return

        finished_task_id = str(task_id or "")
        limit = (
            INGEST_COMPLETION_RESULT_LENGTH
            if str(getattr(self, "name", "")) == TaskName.WRITE_INGEST.value
            else MAX_COMPLETION_RESULT_LENGTH
        )
        try:
            current_app.send_task(
                TaskName.ADMISSION_DISPATCH.value,
                kwargs={
                    "task_id": finished_task_id,
                    "state": status,
                    "result": _json_safe(retval, max_length=limit),
                    "error": _error_text(einfo, retval, status=status),
                    "msg_id": msg_id,
                },
                queue=QueueName.MEMORY_ADMISSION.value,
                priority=int(TaskPriority.NORMAL),
            )
        except Exception:
            logger.warning(
                "failed to publish admission completion task_id=%s",
                finished_task_id,
                exc_info=True,
                extra={"event": "admission_completion_publish_failed"},
            )