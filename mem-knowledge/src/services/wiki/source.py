"""Source lifecycle handoff and progress adaptation without holding model-time sessions."""

from __future__ import annotations

import asyncio
import logging

from ...models.owned import Document, Knowledge, WikiDocumentState
from ...rag.wiki.config import WikiError, WikiSuperseded, read_wiki_config
from ...repositories.wiki import state as repository
from ...repositories.wiki.common import append_log, as_uuid, lock_document, lock_knowledge
from ...tasks.observability import current_parent_task_id
from .runtime import transaction

logger = logging.getLogger(__name__)


class WikiHandoffPending(RuntimeError):
    """Source has committed; retry only its Wiki message handoff."""


def _source_start(session, document_id, source_task_id, *, replace=False):
    document = session.get(Document, as_uuid(document_id))
    if document is None or document.status != 1:
        return None
    knowledge = session.get(Knowledge, document.kb_id)
    state = session.get(WikiDocumentState, document.id)
    if state is None and not read_wiki_config(knowledge.parser_config).enabled:
        return None
    result = repository.source_started(
        session, document.kb_id, document.id, source_task_id, replace=replace
    )
    return result


def begin_source(runtime, document_id, source_task_id):
    return transaction(runtime, _source_start, document_id, source_task_id)


def _incomplete_source(session, document_id, source_task_id):
    document = session.get(Document, as_uuid(document_id))
    if document is None:
        return
    knowledge, _ = lock_knowledge(session, document.kb_id)
    document, state = lock_document(session, document.kb_id, document.id)
    if state is None:
        return
    if state.source_task_id != source_task_id or document.run == 2:
        raise WikiSuperseded("WIKI_SOURCE_TASK_SUPERSEDED", retryable=False)
    state.source_ready = False
    state.status, state.error_code = "failed", "WIKI_SOURCE_INCOMPLETE"
    state.error_stage = "source"
    from ...repositories.wiki.common import current_tasks

    for task in current_tasks(session, state):
        task.status, task.error_code, task.error_stage = "failed", state.error_code, "source"
    document.run = 0
    document.progress = -1.0 if read_wiki_config(knowledge.parser_config).enabled else 1.0
    state.last_progress = document.progress
    append_log(
        document, "QA source partially imported; Wiki generation requires a complete source."
    )


def incomplete_source(runtime, document_id, source_task_id):
    """Keep the source import result while recording the independent Wiki failure."""
    transaction(runtime, _incomplete_source, document_id, source_task_id)


def prepare_source_dispatch(runtime, document_id, source_task_id):
    return transaction(runtime, _source_start, document_id, source_task_id, replace=True)


def _source_complete(session, document_id, source_task_id):
    document = session.get(Document, as_uuid(document_id))
    if document is None or document.status != 1:
        return None
    return repository.source_completed(session, document.kb_id, document.id, source_task_id)


def finish_source(
    runtime, document_id, source_task_id, *, expected_chunk_ids=None, replace_source=False
):
    info = transaction(runtime, _source_verification, document_id, source_task_id)
    if info:
        from ...rag.wiki.input import read_sources
        from ..document import delete_document_search_data
        from ..document_mutation_guard import sync_document_mutation_guard

        if replace_source and expected_chunk_ids == [] and not info["manual_chunks_changed"]:
            # A legitimate zero-chunk reparse must retire the previous ES source first.
            with sync_document_mutation_guard(
                runtime.redis.sync_client(), as_uuid(document_id)
            ) as guard:
                latest = transaction(runtime, _source_verification, document_id, source_task_id)
                if latest:
                    info = latest

                async def remove_old_chunks():
                    await delete_document_search_data(
                        await runtime.elasticsearch.client(),
                        as_uuid(info["kb_id"]),
                        as_uuid(document_id),
                    )

                if latest and not latest["manual_chunks_changed"]:
                    runtime.run_async(remove_old_chunks)
                guard.ensure_owned()
        manual_change = info["manual_chunks_changed"]
        try:
            runtime.run_async(
                lambda: read_sources(
                    runtime, info["kb_id"], document_id,
                    expected_ids=None if manual_change else expected_chunk_ids,
                    exact=replace_source and not manual_change,
                    expect_index=bool(info["chunk_count"]),
                )
            )
        except WikiError as exc:
            # 人工写入可能发生在快照读取与 ES 校验之间，再核对一次持久化标记。
            latest = transaction(runtime, _source_verification, document_id, source_task_id)
            if exc.code != "WIKI_SOURCE_INCOMPLETE" or not latest or not latest[
                "manual_chunks_changed"
            ]:
                raise
            runtime.run_async(lambda: read_sources(
                runtime, latest["kb_id"], document_id, expect_index=bool(latest["chunk_count"])
            ))
    from .execution import publish_map

    # Persist source readiness before attempting the broker handoff.
    delivery = transaction(runtime, _source_complete, document_id, source_task_id)
    try:
        publish_map(runtime, delivery)
    except Exception as exc:
        raise WikiHandoffPending("Wiki handoff pending; source is already committed") from exc


def _source_verification(session, document_id, source_task_id):
    document = session.get(Document, as_uuid(document_id))
    if document is None:
        raise WikiSuperseded("WIKI_DOCUMENT_UNAVAILABLE", retryable=False)
    if document.status != 1:
        return None
    state = session.get(WikiDocumentState, document.id)
    if state and state.source_task_id != source_task_id:
        raise WikiSuperseded("WIKI_SOURCE_TASK_SUPERSEDED", retryable=False)
    if state and state.source_ready:
        return None
    knowledge = session.get(Knowledge, document.kb_id)
    if state is None and not read_wiki_config(knowledge.parser_config).enabled:
        return None
    return {
        "kb_id": str(document.kb_id), "chunk_count": document.chunk_num,
        "manual_chunks_changed": (
            state is not None and "chunk_mutation" in (state.map_progress or {})
        ),
    }


def _handoff_failed(session, document_id, task_id):
    document = session.get(Document, as_uuid(document_id))
    if document is None:
        return
    lock_knowledge(session, document.kb_id)
    document, state = lock_document(session, document.kb_id, document.id)
    if (
        state
        and state.source_task_id == task_id
        and state.stage == "map_pending"
        and not state.publish_confirmed
        and document.run != 2
    ):
        document.run, document.progress = 0, -1.0
        state.error_code = "WIKI_DISPATCH_FAILED"
        append_log(
            document, "Source ready; Wiki dispatch exhausted retries. Retry Wiki explicitly."
        )


def handoff_failed(runtime, document_id, task_id):
    transaction(runtime, _handoff_failed, document_id, task_id)


def apply_source_update(session, document, updater, task_id=None):
    """Adapt existing processor callbacks in their own short transaction."""
    task_id = task_id or current_parent_task_id()
    state = session.get(WikiDocumentState, document.id)
    if state is None:
        updater(document)
        return
    lock_knowledge(session, document.kb_id)
    document, state = lock_document(session, document.kb_id, document.id)
    if state.source_ready and state.source_task_id is None:
        # Backfill may have admitted a source between its commit and final source callbacks.
        return
    if (
        state.source_task_id != task_id
        or document.status != 1
        or document.run == 2
        or state.status in {"cancelled", "failed"}
    ):
        raise WikiSuperseded("WIKI_SOURCE_TASK_SUPERSEDED", retryable=False)
    if state.source_ready:
        return
    previous = max(0.0, state.last_progress)
    updater(document)
    if document.progress >= 0:
        document.progress = max(previous, min(0.7, document.progress * 0.7))
        document.run = 1
        state.last_progress = document.progress
    else:
        state.status, state.error_code = "failed", "WIKI_SOURCE_FAILED"


def handle_source_event(session, document, event, messages):
    """Return whether a Wiki source round owns the event's document progress."""
    state = session.get(WikiDocumentState, document.id)
    if state is None:
        return False
    lock_knowledge(session, document.kb_id)
    document, state = lock_document(session, document.kb_id, document.id)
    if state.source_task_id != event.context.task_id or document.run == 2:
        return True
    if state.source_ready or state.status in {"cancelled", "ready", "skipped", "failed"}:
        return True
    if event.event == "kb_task_failed":
        state.status, state.error_code = "failed", event.error_code or "WIKI_SOURCE_FAILED"
        document.run, document.progress = 0, -1.0
    elif event.event != "kb_task_finished":
        document.run = 1
        if event.progress is not None:
            document.progress = max(state.last_progress, min(0.7, float(event.progress) * 0.7))
            state.last_progress = document.progress
    for message in messages:
        append_log(document, message.rstrip("\n"))
    return True


async def register_chunk_change(runtime, kb_id, document_id, *, incomplete=False):
    """ES 已发生变更；独立记录派生状态失败，不能诱导客户端重复新增分块。"""
    try:
        return await asyncio.to_thread(
            transaction, runtime, repository.chunks_changed, kb_id, document_id,
            incomplete=incomplete,
        )
    except WikiSuperseded:
        # 文档/知识库同时撤回时，由其生命周期负责清理，不能重新生成。
        return None
    except Exception as exc:
        logger.exception(
            "分块变更后 Wiki 登记失败 stage=chunk_wiki_register kb_id=%s document_id=%s "
            "error_type=%s reason=%s", kb_id, document_id, type(exc).__name__, exc,
        )
        return None


async def publish_chunk_change(runtime, delivery):
    if delivery is None:
        return
    from .execution import publish_map

    try:
        await asyncio.to_thread(publish_map, runtime, delivery, terminal_on_failure=True)
    except Exception as exc:
        logger.exception(
            "分块变更已登记，Wiki 投递失败 stage=chunk_wiki_publish kb_id=%s document_id=%s "
            "task_id=%s error_type=%s reason=%s", delivery["kb_id"], delivery["document_id"],
            delivery["task_id"], type(exc).__name__, exc,
        )
