"""Atomic source rounds, generation admission and message handoffs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from ...models.owned import (
    WikiDocumentPageTask,
    WikiDocumentState,
    WikiPage,
    WikiPageSource,
)
from ...rag.wiki.config import (
    ACTIVE_STATUSES,
    TASK_CLEANUP_BATCH_SIZE,
    TASK_RETENTION_DAYS,
    TERMINAL_TASK_STATUSES,
    WikiError,
    WikiSourcePending,
    WikiSuperseded,
    generation_config_hash,
    read_wiki_config,
)
from ...utils.datetime_utils import utcnow_naive
from .common import (
    append_log,
    as_uuid,
    current_tasks,
    lock_document,
    lock_knowledge,
    require_round,
    settle_progress,
    state_delivery,
)

DISPATCH_RECOVERY_SECONDS = 120


def _active_delivery(document, state, *, explicit, admission_id):
    if state is None or state.status not in ACTIVE_STATUSES or document.run == 2:
        return None
    if state.stage == "waiting_source":
        if (
            explicit and not state.source_ready and state.source_task_id
            and document.progress >= 0
        ):
            return {
                **state_delivery(state), "task_id": state.source_task_id,
                "publish": False, "accepted": False,
            }
        return None
    if state.stage == "waiting_wiki" or not state.task_id:
        return None
    # Give the original publisher time to finish before recovering an abandoned handoff.
    updated_at = getattr(state, "updated_at", None)
    abandoned = updated_at is not None and updated_at <= (
        utcnow_naive() - timedelta(seconds=DISPATCH_RECOVERY_SECONDS)
    )
    publish = (
        state.stage == "map_pending" and state.status == "pending"
        and not state.publish_confirmed
        and (
            state.error_code == "WIKI_DISPATCH_FAILED"
            or (admission_id is not None and state.admission_id == admission_id)
            or (explicit and abandoned)
        )
    )
    if publish and document.progress < 0:
        document.run, document.progress = 0, 0.0
        state.last_progress = 0.0
        append_log(document, "Wiki dispatch retry queued.")
    if publish:
        state.error_code = None
        state.updated_at = utcnow_naive()
    return {**state_delivery(state), "publish": publish, "accepted": False}


def affected_page_ids(session, document_id):
    ids = set(
        session.scalars(
            select(WikiPageSource.page_id).where(WikiPageSource.document_id == document_id)
        )
    )
    ids.update(
        session.scalars(
            select(WikiDocumentPageTask.page_id).where(
                WikiDocumentPageTask.document_id == document_id,
                WikiDocumentPageTask.requested_version
                == select(WikiDocumentState.requested_version)
                .where(WikiDocumentState.document_id == document_id)
                .scalar_subquery(),
                WikiDocumentPageTask.status.not_in({"succeeded", "superseded"}),
            )
        )
    )
    return ids


def dirty_pages(session, page_ids, *, status="updating"):
    if not page_ids:
        return
    for page in session.scalars(
        select(WikiPage).where(WikiPage.id.in_(page_ids)).order_by(WikiPage.id).with_for_update()
    ):
        if page.status not in {"updating", "cleanup_pending"}:
            page.defer_started_at = None
        page.row_version += 1
        page.status = status
        page.failure_attempt, page.defer_count, page.error_code = 0, 0, None


def start_round(session, document, state, scope, *, source=False, reuse=False):
    old_tasks = current_tasks(session, state) if state else []
    old_ids = affected_page_ids(session, document.id)
    if state is None:
        state = WikiDocumentState(document_id=document.id, **scope)
        session.add(state)
        session.flush()
    else:
        state.requested_version += 1
    for task in old_tasks:
        if task.status not in TERMINAL_TASK_STATUSES:
            task.status = "superseded"
    state.processing_mode = "source" if source else "wiki_only"
    state.source_ready = not source
    if source:
        # Only source_started/source_completed may bind the current parse task identity.
        state.source_task_id = None
    state.plan_ready = False
    state.mapped_version = 0
    state.task_id = None
    state.admission_id = None
    state.publish_confirmed = False
    state.status = "pending"
    state.stage = "waiting_source" if source else "waiting_wiki"
    state.operation = "upsert"
    state.failure_attempt, state.defer_count = 0, 0
    state.error_code, state.error_stage = None, None
    state.last_progress = 0.0
    if not reuse:
        state.map_progress = {}
        state.config_hash = None
        state.input_hash = None
    else:
        state.map_progress = {}
    # Keep outstanding old cleanup targets even before the new candidate set is fixed.
    by_page = {task.page_id: task for task in old_tasks}
    for page_id in sorted(old_ids, key=str):
        old = by_page.get(page_id)
        session.add(
            WikiDocumentPageTask(
                **scope,
                document_id=document.id,
                requested_version=state.requested_version,
                page_id=page_id,
                operation=old.operation if reuse and old else "retract",
                status="pending",
            )
        )
    if reuse:
        for item in session.scalars(
            select(WikiPageSource).where(WikiPageSource.document_id == document.id)
        ):
            item.document_wiki_version = state.requested_version
    dirty_pages(session, old_ids)
    document.run, document.progress = 0, 0.0
    document.process_begin_at = utcnow_naive()
    document.process_duration = 0.0
    document.progress_msg = ""
    append_log(document, "Wiki processing round created.")
    session.flush()
    expired = (
        select(WikiDocumentPageTask.subtask_id)
        .where(
            WikiDocumentPageTask.document_id == document.id,
            WikiDocumentPageTask.requested_version < state.requested_version,
            WikiDocumentPageTask.status.in_(TERMINAL_TASK_STATUSES),
            WikiDocumentPageTask.updated_at < utcnow_naive() - timedelta(days=TASK_RETENTION_DAYS),
        )
        .order_by(WikiDocumentPageTask.updated_at, WikiDocumentPageTask.subtask_id)
    )
    session.execute(
        delete(WikiDocumentPageTask)
        .where(WikiDocumentPageTask.subtask_id.in_(expired.limit(TASK_CLEANUP_BATCH_SIZE)))
        .execution_options(synchronize_session=False)
    )
    return state


def admit(
    session, kb_id, document_id, *, explicit=False, admission_id=None, expected_scope=None,
    source_task_id=None, admission_at_ms=None,
):
    knowledge, scope = lock_knowledge(session, kb_id, expected_scope)
    document, state = lock_document(session, kb_id, document_id)
    if document.status == 1 or (explicit and state and state.operation == "retract"):
        active = _active_delivery(document, state, explicit=explicit, admission_id=admission_id)
        if active:
            return active
    if document.status != 1:
        if (
            explicit
            and state
            and state.operation == "retract"
            and state.status in {"pending", "running", "retry_wait", "failed", "cancelled"}
        ):
            if state.status in {"failed", "cancelled"}:
                source_ready = state.source_ready
                state = start_round(session, document, state, scope)
                state.source_ready = source_ready
                state.operation = "retract"
                state.plan_ready = True
                state.mapped_version = state.requested_version
            state.task_id = state.task_id or str(uuid.uuid4())
            state.status, state.stage = "pending", "map_pending"
            state.publish_confirmed = False
            document.run = 0
            session.flush()
            return {**state_delivery(state), "publish": True, "accepted": True}
        return None
    if state is None and explicit and document.run in {0, 1} and 0 <= document.progress < 1:
        if source_task_id:
            return {
                "kb_id": str(kb_id), "document_id": str(document_id),
                "task_id": source_task_id, "publish": False, "accepted": False,
            }
        # The service may resolve a parse task registered while Wiki was disabled.
        raise WikiSourcePending("WIKI_SOURCE_NOT_READY", retryable=False)
    if not read_wiki_config(knowledge.parser_config).enabled:
        if explicit:
            raise WikiError("WIKI_DISABLED", retryable=False)
        return None
    if state and state.status not in ACTIVE_STATUSES:
        started_at = getattr(document, "process_begin_at", None)
        predates_event = (
            admission_at_ms is None or started_at is None
            or started_at <= datetime.fromtimestamp(
                admission_at_ms / 1000, UTC
            ).replace(tzinfo=None)
        )
        recover_failed = (
            admission_id is not None and state.admission_id != admission_id
            and state.status == "failed" and state.source_ready
            and state.operation == "upsert" and document.run != 2
            and predates_event
        )
        if not explicit and not recover_failed:
            return None
        if not state.source_ready:
            raise WikiError("WIKI_SOURCE_NOT_READY", retryable=False)
        reuse = (
            state.mapped_version == state.requested_version
            and state.config_hash
            == generation_config_hash(knowledge.llm_id, knowledge.parser_config)
        )
        state = start_round(session, document, state, scope, reuse=reuse)
    elif state is None:
        if document.run != 0 or document.progress != 1:
            if explicit:
                raise WikiError("WIKI_SOURCE_NOT_READY", retryable=False)
            return None
        state = start_round(session, document, None, scope)
    if not state.source_ready or document.run == 2:
        if explicit:
            raise WikiError("WIKI_SOURCE_NOT_READY", retryable=False)
        return None
    if knowledge.llm_id is None:
        raise WikiError("WIKI_MODEL_REQUIRED", retryable=False)
    if state.processing_mode == "wiki_only" and document.progress == 1:
        document.progress = 0.0
        state.last_progress = 0.0
        document.process_begin_at = utcnow_naive()
        document.process_duration = 0.0
        document.progress_msg = ""
        append_log(document, "Wiki-only processing round started.")
    state.task_id = str(uuid.uuid4())
    state.admission_id = admission_id
    state.publish_confirmed = False
    state.stage = "map_pending"
    state.status = "pending"
    document.run = 1 if state.processing_mode == "source" else 0
    append_log(document, "Wiki queued.")
    session.flush()
    return {**state_delivery(state), "publish": True, "accepted": True}


def confirm_publish(session, delivery):
    lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    if (
        state
        and state.requested_version == delivery["requested_version"]
        and state.task_id == delivery["task_id"]
    ):
        state.publish_confirmed = True


def record_publish_failure(session, delivery, *, terminal=False):
    lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    if (
        state
        and state.requested_version == delivery["requested_version"]
        and state.task_id == delivery["task_id"]
        and state.stage == "map_pending"
        and state.status == "pending"
        and not state.publish_confirmed
    ):
        # Retain the task identity: an ambiguous broker error may still deliver it.
        # Explicit retry republishes the same round instead of duplicating its work.
        state.error_code = "WIKI_DISPATCH_FAILED"
        if terminal:
            document.run, document.progress = 0, -1.0
        append_log(document, "Wiki dispatch failed; retry is available.")


def chunks_changed(session, kb_id, document_id, *, incomplete=False):
    """调用者持有文档写入互斥；每次实际变更推进轮次，在途源处理仍由原任务交接。"""
    knowledge, scope = lock_knowledge(session, kb_id)
    document, state = lock_document(session, kb_id, document_id)
    if document.status != 1 or document.run == 2:
        return None
    if state is not None and not state.source_ready:
        # 保留源任务身份；完成交接时应读取人工变更后的最终分块，而非解析器最初的 ID 集合。
        progress = dict(state.map_progress or {})
        previous = progress.get("chunk_mutation", {})
        progress["chunk_mutation"] = {"incomplete": incomplete or previous.get("incomplete", False)}
        state.map_progress = progress
        return None
    if not read_wiki_config(knowledge.parser_config).enabled:
        # 已有素材仍须失效；开启 Wiki 时可从 waiting_wiki 继续，不再读取旧页面。
        if state is not None and state.source_ready:
            fields = ("run", "progress", "process_begin_at", "process_duration", "progress_msg")
            previous = {name: getattr(document, name) for name in fields}
            start_round(session, document, state, scope)
            for name, value in previous.items():
                setattr(document, name, value)
        return None
    if state is None and (document.run == 1 or document.progress != 1):
        return None
    state = start_round(session, document, state, scope)
    error_code = "WIKI_SOURCE_INCOMPLETE" if incomplete else (
        "WIKI_MODEL_REQUIRED" if knowledge.llm_id is None else None
    )
    if error_code:
        state.status, state.error_code, state.error_stage = "failed", error_code, "chunk_mutation"
        document.run, document.progress = 0, -1.0
        for task in current_tasks(session, state):
            task.status, task.error_code, task.error_stage = "failed", error_code, "chunk_mutation"
        append_log(document, f"分块写入后 Wiki 等待显式重试：{error_code}")
        return None
    session.flush()
    return admit(session, kb_id, document_id)


def source_started(session, kb_id, document_id, source_task_id, *, replace=False):
    knowledge, scope = lock_knowledge(session, kb_id)
    document, state = lock_document(session, kb_id, document_id)
    enabled = read_wiki_config(knowledge.parser_config).enabled
    if not enabled and state is None:
        return None
    if state and source_task_id and state.source_task_id == source_task_id:
        if state.status == "failed" and not state.source_ready:
            raise WikiSuperseded("WIKI_SOURCE_ALREADY_FAILED", retryable=False)
        return {
            **state_delivery(state),
            "source_ready": state.source_ready,
            "wiki_enabled": enabled,
        }
    if state and not replace:
        raise WikiSuperseded("WIKI_SOURCE_TASK_SUPERSEDED", retryable=False)
    state = start_round(session, document, state, scope, source=True)
    state.source_task_id = source_task_id
    return {**state_delivery(state), "source_ready": False, "wiki_enabled": enabled}


def source_completed(session, kb_id, document_id, source_task_id):
    knowledge, scope = lock_knowledge(session, kb_id)
    document, state = lock_document(session, kb_id, document_id)
    enabled = read_wiki_config(knowledge.parser_config).enabled
    if state is None:
        if not enabled:
            return None
        already_complete = document.run == 0 and document.progress == 1
        state = start_round(session, document, None, scope, source=not already_complete)
        state.source_task_id = source_task_id
    if state.source_task_id != source_task_id:
        return None
    if not state.source_ready and (state.map_progress or {}).get("chunk_mutation", {}).get(
        "incomplete", False
    ):
        state.status, state.error_code, state.error_stage = (
            "failed", "WIKI_SOURCE_INCOMPLETE", "chunk_mutation"
        )
        document.run, document.progress = 0, -1.0 if enabled else 1.0
        state.last_progress = document.progress
        for task in current_tasks(session, state):
            task.status, task.error_code, task.error_stage = (
                "failed", state.error_code, "chunk_mutation"
            )
        append_log(document, "解析期间分块部分写入失败，Wiki 来源需重新确认。")
        return None
    if state.source_ready and state.stage not in {"waiting_source", "waiting_wiki"}:
        return {**state_delivery(state), "publish": not state.publish_confirmed, "accepted": False}
    require_round(document, state, state.requested_version)
    state.source_ready = True
    state.stage = "waiting_wiki"
    if not enabled:
        state.processing_mode = "wiki_only"
        state.last_progress = 0.0
        document.run, document.progress = 0, 1.0
        append_log(document, "Source ready; Wiki generation is disabled.")
        return None
    document.progress = 0.7 if state.processing_mode == "source" else 0.0
    state.last_progress = document.progress
    session.flush()
    return admit(session, kb_id, document_id)


def withdraw(session, kb_id, document_id, *, hard_delete=False):
    """Called inside the document status/deletion transaction, before source records vanish."""
    if session.get(WikiDocumentState, as_uuid(document_id)) is None:
        return []
    _, scope = lock_knowledge(session, kb_id, allow_deleted=True)
    document, state = lock_document(session, kb_id, document_id)
    if state is None:
        return []
    pages = affected_page_ids(session, document.id)
    if hard_delete:
        session.execute(delete(WikiPageSource).where(WikiPageSource.document_id == document.id))
        dirty_pages(session, pages, status="cleanup_pending")
        session.delete(state)
    else:
        source_ready = state.source_ready
        state = start_round(session, document, state, scope)
        state.source_ready = source_ready
        session.execute(delete(WikiPageSource).where(WikiPageSource.document_id == document.id))
        state.operation = "retract"
        state.task_id = str(uuid.uuid4())
        state.plan_ready = True
        state.mapped_version = state.requested_version
        state.stage = "reducing"
        state.status = "running"
        document.run = 1
        dirty_pages(session, pages)
    session.flush()
    if not hard_delete:
        settle_progress(session, document, state)
    return [str(page_id) for page_id in sorted(pages, key=str)]


def reenable(session, kb_id, document_id):
    knowledge, scope = lock_knowledge(session, kb_id, allow_deleted=True)
    if knowledge.status == 2:
        return None
    document, state = lock_document(session, kb_id, document_id)
    if state:
        ready = state.source_ready
        state = start_round(session, document, state, scope, source=not ready)
    session.flush()
    return admit(session, kb_id, document_id)


def cancel(session, kb_id, document_id):
    if session.get(WikiDocumentState, as_uuid(document_id)) is None:
        return []
    lock_knowledge(session, kb_id, allow_deleted=True)
    document, state = lock_document(session, kb_id, document_id)
    if not state or state.status not in ACTIVE_STATUSES:
        return []
    state.status = "cancelled"
    document.run = 2
    page_ids = set()
    for task in current_tasks(session, state):
        if task.status != "succeeded":
            page_ids.add(task.page_id)
        if task.status not in TERMINAL_TASK_STATUSES:
            task.status = "cancelled"
    append_log(document, "Wiki processing cancelled.")
    if state.mapped_version == state.requested_version and state.source_ready:
        dirty_pages(session, page_ids, status="cleanup_pending")
        return [str(page_id) for page_id in sorted(page_ids, key=str)]
    return []
