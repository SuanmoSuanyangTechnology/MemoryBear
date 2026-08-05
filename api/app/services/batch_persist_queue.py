"""Phase 3: Asynchronous batch persist queue.

Design doc: docs/方案设计_高并发性能优化.md Section 8

Single asyncio.Queue consumer that collects ``PersistTask`` entries after
Phase 2 streaming completes and flushes them to the database in batches.
Message-persistence tasks within a batch are merged into a single multi-row
INSERT plus atomic conversation counter UPDATEs.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as uuid_module
from collections import defaultdict
from dataclasses import dataclass, field
from app.core.utils.datetime_utils import utcnow_naive
from typing import Any, ClassVar

from sqlalchemy import (
    select as sa_select,
    insert as sa_insert,
    update as sa_update,
    case as sa_case,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db import get_async_db_context

logger = logging.getLogger(__name__)


def _get_async_pool_status_safe() -> dict | None:
    try:
        from app.db import get_async_pool_status
        return get_async_pool_status()
    except Exception:
        return None


def _fmt_pool(status: dict | None) -> str:
    if not status:
        return "N/A"
    return (
        f"checkout={status.get('checked_out')}/{status.get('pool_size')}"
        f" overflow={status.get('overflow')}"
        f" usage={status.get('usage_percent')}%"
    )


# ---------------------------------------------------------------------------
# PersistTask
# ---------------------------------------------------------------------------

@dataclass
class PersistTask:
    """Immutable description of a single persistence operation."""

    task_type: str
    """One of: save_messages, save_agent_execution, record_usage, after_turn, save_failed_message."""

    args: dict[str, Any]
    """Keyword arguments for the handler that executes this task."""

    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# BatchPersistQueue (singleton)
# ---------------------------------------------------------------------------

class BatchPersistQueue:
    """Singleton async batch writer for Phase 3 persistence.

    Usage::

        # Startup (in FastAPI lifespan)
        await BatchPersistQueue.start()

        # Enqueue (in controller event_generator after streaming)
        await BatchPersistQueue.enqueue(PersistTask(
            task_type="save_messages",
            args={"ctx": ctx, "result": result, ...},
        ))

        # Shutdown (in FastAPI lifespan)
        await BatchPersistQueue.stop()
    """

    _instance: ClassVar[BatchPersistQueue | None] = None

    def __init__(self) -> None:
        self._queue: asyncio.Queue[PersistTask | None] = asyncio.Queue(
            maxsize=settings.BATCH_PERSIST_QUEUE_SIZE
        )
        self._consumer_task: asyncio.Task[Any] | None = None
        self._running = False

    # -- singleton access -------------------------------------------------------

    @classmethod
    def _get(cls) -> BatchPersistQueue:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- public API -------------------------------------------------------------

    @classmethod
    async def start(cls) -> None:
        inst = cls._get()
        if inst._running:
            return
        inst._running = True
        inst._consumer_task = asyncio.create_task(inst._consumer())
        logger.info("BatchPersistQueue consumer started")

    @classmethod
    async def enqueue(cls, task: PersistTask) -> None:
        """Non-blocking enqueue with degradation to sync write on full queue."""
        inst = cls._get()
        if not inst._running:
            logger.warning("BatchPersistQueue not running, falling back to sync write")
            await inst._sync_write(task)
            return
        try:
            inst._queue.put_nowait(task)
        except asyncio.QueueFull:
            timeout_ms = settings.BATCH_PERSIST_PUT_TIMEOUT_MS
            try:
                await asyncio.wait_for(inst._queue.put(task), timeout=timeout_ms / 1000)
            except asyncio.TimeoutError:
                logger.warning(
                    "BatchPersistQueue full after %sms, degrading to sync write",
                    timeout_ms,
                )
                await inst._sync_write(task)

    @classmethod
    async def flush(cls) -> None:
        """Drain remaining tasks from the queue (used during shutdown)."""
        inst = cls._get()
        tasks: list[PersistTask] = []
        while not inst._queue.empty():
            try:
                tasks.append(inst._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if tasks:
            logger.info("Flushing %d remaining persist tasks", len(tasks))
            await inst._batch_write(tasks)

    @classmethod
    async def stop(cls) -> None:
        """Graceful shutdown: drain queue, stop consumer."""
        inst = cls._get()
        if not inst._running:
            return
        inst._running = False
        await inst._queue.put(None)  # sentinel
        if inst._consumer_task:
            try:
                await asyncio.wait_for(inst._consumer_task, timeout=30)
            except asyncio.TimeoutError:
                logger.error("BatchPersistQueue consumer did not stop within 30s")
                inst._consumer_task.cancel()
        logger.info("BatchPersistQueue consumer stopped")

    # -- consumer ---------------------------------------------------------------

    async def _consumer(self) -> None:
        """Background coroutine that collects tasks in batches and writes to DB."""
        max_batch = settings.BATCH_PERSIST_MAX_BATCH
        max_wait = settings.BATCH_PERSIST_MAX_WAIT_MS / 1000

        while self._running:
            batch: list[PersistTask] = []
            try:
                # Wait for first task
                first = await asyncio.wait_for(self._queue.get(), timeout=max_wait)
            except asyncio.TimeoutError:
                continue

            if first is None:  # sentinel
                break

            batch.append(first)

            # Collect remaining tasks up to max_batch
            deadline = time.time() + max_wait
            while len(batch) < max_batch and time.time() < deadline:
                try:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    task = await asyncio.wait_for(self._queue.get(), timeout=min(remaining, 0.05))
                    if task is None:  # sentinel
                        self._running = False
                        break
                    batch.append(task)
                except asyncio.TimeoutError:
                    break

            if batch:
                await self._batch_write(batch)

        # Final drain
        await self._drain_remaining()

    async def _drain_remaining(self) -> None:
        tasks: list[PersistTask] = []
        while not self._queue.empty():
            try:
                task = self._queue.get_nowait()
                if task is not None:
                    tasks.append(task)
            except asyncio.QueueEmpty:
                break
        if tasks:
            await self._batch_write(tasks)

    # -- batch write ------------------------------------------------------------

    async def _batch_write(self, batch: list[PersistTask]) -> None:
        """Write a batch of tasks in a single DB session.

        Message-persistence tasks (save_messages / save_failed_message) are
        extracted and committed together via one multi-row INSERT plus atomic
        conversation counter UPDATEs — the DB is opened only once per batch.
        """
        if not batch:
            return

        t0 = time.time()
        msg_tasks = [t for t in batch if t.task_type in ("save_messages", "save_failed_message")]
        other_tasks = [t for t in batch if t not in msg_tasks]

        pool_status_before = _get_async_pool_status_safe()

        try:
            async with get_async_db_context() as db:
                if msg_tasks:
                    await _bulk_persist_messages(db, msg_tasks)
                for task in other_tasks:
                    try:
                        await self._execute_task(db, task)
                    except Exception:
                        logger.exception(
                            "Failed to execute persist task %s (args keys: %s)",
                            task.task_type,
                            list(task.args.keys()),
                        )
                await db.commit()

            elapsed_ms = (time.time() - t0) * 1000
            pool_status_after = _get_async_pool_status_safe()
            logger.info(
                "[TIMING] batch_persist flush: batch=%d msg=%d other=%d elapsed=%.1fms "
                "pool_before=%s pool_after=%s queue_depth=%d",
                len(batch), len(msg_tasks), len(other_tasks), elapsed_ms,
                _fmt_pool(pool_status_before), _fmt_pool(pool_status_after),
                self._queue.qsize(),
            )
        except Exception:
            elapsed_ms = (time.time() - t0) * 1000
            logger.exception(
                "[TIMING] batch_persist FAILED: batch=%d msg=%d elapsed=%.1fms queue_depth=%d",
                len(batch), len(msg_tasks), elapsed_ms, self._queue.qsize(),
            )

    async def _sync_write(self, task: PersistTask) -> None:
        """Synchronous (immediate) write fallback for a single task."""
        try:
            async with get_async_db_context() as db:
                if task.task_type in ("save_messages", "save_failed_message"):
                    await _bulk_persist_messages(db, [task])
                else:
                    await self._execute_task(db, task)
                await db.commit()
        except Exception:
            logger.exception("Sync persist failed for task %s", task.task_type)

    # -- task handlers ----------------------------------------------------------

    async def _execute_task(self, db: Any, task: PersistTask) -> None:
        """Dispatch a single task to its handler."""
        handler = _TASK_HANDLERS.get(task.task_type)
        if handler is None:
            logger.warning("Unknown persist task type: %s", task.task_type)
            return
        await handler(db, **task.args)


# ---------------------------------------------------------------------------
# Bulk message persistence
# ---------------------------------------------------------------------------


def _row(
    msg_id: uuid_module.UUID,
    conversation_id: uuid_module.UUID,
    role: str,
    content: str,
    meta_data: dict[str, Any] | None = None,
    status: str = "completed",
    parent_message_id: uuid_module.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "meta_data": meta_data or {},
        "status": status,
        "parent_message_id": parent_message_id,
        "created_at": utcnow_naive(),
    }


async def _bulk_persist_messages(
    db: Any,
    msg_tasks: list[PersistTask],
) -> None:
    """Persist all messages from *msg_tasks* via a single multi-row INSERT
    followed by atomic ``message_count`` UPDATEs per conversation.

    Idempotent on ``messages.id``: the INSERT uses ``ON CONFLICT DO NOTHING``
    and rows are deduplicated within the batch, so re-enqueued/replayed tasks
    (same user_message_id/message_id) neither raise ``UniqueViolationError``
    nor roll back the whole batch. ``message_count`` is incremented only for
    rows actually inserted (via ``RETURNING id``).
    """
    from app.models.conversation_model import Message, Conversation
    from app.services.chat_context import StreamResult

    rows: list[dict[str, Any]] = []
    row_conv: dict[uuid_module.UUID, uuid_module.UUID] = {}
    conv_titles: dict[uuid_module.UUID, str | None] = {}

    def _add_row(row: dict[str, Any], conv_id: uuid_module.UUID) -> None:
        rows.append(row)
        mid = row["id"]
        if isinstance(mid, str):
            mid = uuid_module.UUID(mid)
        row_conv[mid] = conv_id

    for task in msg_tasks:
        args = task.args
        result: StreamResult | None = args.get("result")

        conv_id = args.get("conversation_id_override") or args.get("conversation_id")
        if conv_id is None:
            continue
        if isinstance(conv_id, str):
            conv_id = uuid_module.UUID(conv_id)

        # --- save_failed_message ---
        if task.task_type == "save_failed_message":
            user_msg_id = args.get("user_message_id", uuid_module.uuid4())
            msg_id = args.get("message_id", uuid_module.uuid4())
            user_content = args.get("user_message_content", "")
            _add_row(_row(user_msg_id, conv_id, "user",
                          user_content,
                          {"files": args.get("files_meta", [])}), conv_id)
            _add_row(_row(msg_id, conv_id, "assistant",
                          args.get("error_message", "An error occurred during generation."),
                          {"error": args.get("error_detail", "")},
                          status="failed"), conv_id)
            conv_titles[conv_id] = None
            continue

        # --- save_messages ---
        user_msg_id = args.get("user_message_id_override") or (
            result.user_message_id if result else uuid_module.uuid4())
        msg_id = args.get("message_id_override") or (
            result.message_id if result else uuid_module.uuid4())

        # user message
        if args.get("user_meta_override"):
            user_meta = dict(args["user_meta_override"])
        else:
            files_meta = args.get("files_meta", [])
            if result and getattr(result, 'files_meta', None):
                files_meta = result.files_meta
            user_meta = {"files": files_meta}
            if result and getattr(result, 'history_files', None):
                user_meta["history_files"] = result.history_files

        user_parent_id = args.get("user_parent_message_id")
        user_content = args.get("user_message_content", "") or ""

        _add_row(_row(user_msg_id, conv_id, "user",
                      user_content, user_meta,
                      parent_message_id=user_parent_id), conv_id)

        # assistant message meta
        if args.get("meta_override"):
            assistant_meta = dict(args["meta_override"])
        elif result and result.assistant_meta:
            assistant_meta = dict(result.assistant_meta)
        else:
            assistant_meta = {
                "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": result.total_tokens if result else 0},
            }

        if result:
            for src, dst in (("suggested_questions", "suggested_questions"),
                             ("citations", "citations"),
                             ("audio_url", "audio_url"),
                             ("audio_status", "audio_status"),
                             ("full_reasoning", "reasoning_content")):
                val = getattr(result, src, None)
                if val:
                    assistant_meta.setdefault(dst, val)
            api_key_model_name = args.get("api_key_model_name")
            if api_key_model_name:
                assistant_meta.setdefault("model", api_key_model_name)

        assistant_content = args.get("content_override") or (
            result.full_content if result else "")
        _add_row(_row(msg_id, conv_id, "assistant", assistant_content, assistant_meta,
                      parent_message_id=user_msg_id), conv_id)

        conv_titles[conv_id] = (
            user_content[:50] + ("..." if len(user_content) > 50 else "")
            if user_content else None
        )

        # opening statement
        if args.get("is_new_conversation") and args.get("opening_statement"):
            opening_id = args.get("opening_message_id")
            if opening_id:
                _add_row(_row(opening_id, conv_id, "assistant", args["opening_statement"]), conv_id)

    # --- dedupe rows within the batch (same task enqueued twice, or success +
    # failed tasks sharing one message pair) ---
    seen_ids: set[uuid_module.UUID] = set()
    unique_rows: list[dict[str, Any]] = []
    for r in rows:
        mid = r["id"]
        if isinstance(mid, str):
            mid = uuid_module.UUID(mid)
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        unique_rows.append(r)
    if len(unique_rows) < len(rows):
        logger.warning(
            "batch_persist: dropped %d duplicate message rows within one batch",
            len(rows) - len(unique_rows),
        )

    # --- single idempotent multi-row INSERT for all messages ---
    inserted_ids: set[uuid_module.UUID] = set()
    if unique_rows:
        result = await db.execute(
            pg_insert(Message)
            .values(unique_rows)
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(Message.id)
        )
        inserted_ids = {row[0] for row in result.all()}
        if len(inserted_ids) < len(unique_rows):
            logger.warning(
                "batch_persist: %d/%d message rows skipped due to existing ids (idempotent skip)",
                len(unique_rows) - len(inserted_ids), len(unique_rows),
            )

    # --- atomic UPDATE per conversation, counting only actually inserted rows ---
    actual_deltas: dict[uuid_module.UUID, int] = defaultdict(int)
    for mid in inserted_ids:
        actual_deltas[row_conv[mid]] += 1

    for conv_id, delta in actual_deltas.items():
        if delta <= 0:
            continue
        values: dict[str, Any] = {
            "message_count": Conversation.message_count + delta,
        }
        if conv_titles.get(conv_id):
            values["title"] = sa_case(
                (Conversation.message_count <= 1, conv_titles[conv_id]),
                else_=Conversation.title,
            )
        await db.execute(
            sa_update(Conversation).where(Conversation.id == conv_id).values(**values)
        )


def _to_uuid(value: Any) -> uuid_module.UUID | None:
    """Coerce a str or UUID to UUID, returning None if falsy."""
    if not value:
        return None
    return uuid_module.UUID(value) if isinstance(value, str) else value


async def _handle_save_agent_execution(
    db: Any,
    **kwargs: Any,
) -> None:
    """Persist agent execution record after messages are committed."""
    from app.models.agent_execution_model import AgentExecution
    from app.core.utils.datetime_utils import parse_timestamp_to_utc_naive, utcnow_naive

    execution = AgentExecution(
        app_id=_to_uuid(kwargs["app_id"]),
        conversation_id=_to_uuid(kwargs["conversation_id"]),
        message_id=_to_uuid(kwargs.get("message_id")),
        agent_config_id=_to_uuid(kwargs.get("agent_config_id")),
        release_id=_to_uuid(kwargs.get("release_id")),
        triggered_by=None,
        steps=kwargs.get("steps", []),
        status=kwargs.get("status", "completed"),
        started_at=parse_timestamp_to_utc_naive(kwargs["started_at_ts"]),
        completed_at=utcnow_naive(),
        elapsed_time=kwargs.get("elapsed_time"),
        token_usage=kwargs.get("token_usage"),
        error_message=kwargs.get("error_message"),
        meta_data=kwargs.get("meta_data", {}),
    )
    db.add(execution)


async def _handle_record_usage(
    db: Any,
    **kwargs: Any,
) -> None:
    """Record API key usage count."""
    api_key_id = kwargs.get("api_key_id")
    if api_key_id is None:
        return
    from app.models.models_model import ModelApiKey

    result = await db.execute(
        sa_select(ModelApiKey).where(ModelApiKey.id == api_key_id)
    )
    api_key = result.scalars().first()
    if api_key is not None:
        api_key.usage_count = (api_key.usage_count or 0) + 1
        db.add(api_key)


async def _handle_after_turn(
    db: Any,
    **kwargs: Any,
) -> None:
    """Run context engine after-turn processing."""
    try:
        from app.services.context_engine_manager import ContextEngineManager

        manager = ContextEngineManager(db)
        await manager.after_app_turn(
            features=kwargs.get("features_config", {}),
            conversation_id=kwargs.get("conversation_id"),
            current_provider=kwargs.get("api_key_provider"),
            current_is_omni=kwargs.get("api_key_is_omni", False),
            model_config_id=kwargs.get("model_config_id"),
        )
    except Exception:
        logger.exception("after_turn failed for conversation %s", kwargs.get("conversation_id"))


async def _handle_save_node_executions(
    db: Any,
    execution_id: str,
    items: list[dict[str, Any]],
    **kwargs: Any,
) -> None:
    """Batch persist workflow node execution records."""
    from app.models.workflow_model import WorkflowNodeExecution

    if not items:
        return
    await db.execute(
        sa_text("DELETE FROM workflow_node_executions WHERE execution_id = :eid"),
        {"eid": execution_id},
    )
    await db.execute(sa_insert(WorkflowNodeExecution).values(items))


_TASK_HANDLERS: dict[str, Any] = {
    "save_agent_execution": _handle_save_agent_execution,
    "save_node_executions": _handle_save_node_executions,
    "record_usage": _handle_record_usage,
    "after_turn": _handle_after_turn,
}
