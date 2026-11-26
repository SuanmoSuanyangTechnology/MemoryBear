"""按库领取文档；共享页面计划，分别提交来源与进度。"""

import asyncio
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import copy_context
from copy import deepcopy
from threading import BoundedSemaphore
from uuid import uuid4

from sqlalchemy import or_, select

from ...integrations.model.usage_context import bind_usage_context
from ...models.owned import Document, WikiDocumentState
from ...models.owned.wiki import utcnow
from ...rag.wiki.config import BATCH_TASK, MAP_QUEUE, WikiDeferred, WikiError, WikiSuperseded
from ...rag.wiki.diagnostics import diagnostic_json, exception_details
from ...rag.wiki.identity import topic_identity
from ...rag.wiki.planning import reconcile, reuse_qualified_legacy_pages, taxonomy
from ...rag.wiki.resolution import resolve_candidates
from ...repositories.wiki import deliveries, failures, pages
from ...repositories.wiki.common import as_uuid, lock_document, lock_knowledge, state_delivery
from . import execution
from .lease import map_batch_lease, map_lease, taxonomy_lease
from .runtime import transaction

logger = logging.getLogger(__name__)


def wake_batch(runtime, kb_id, delay=None):
    execution.dispatcher(runtime).send_sync(
        BATCH_TASK,
        kwargs={"kb_id": str(kb_id)},
        queue=MAP_QUEUE,
        countdown=runtime.settings.kb_wiki_batch_delay_seconds if delay is None else delay,
    )


def eligible(kb_id):
    state = WikiDocumentState
    return (
        select(state)
        .join(Document, Document.id == state.document_id)
        .where(
            state.kb_id == as_uuid(kb_id),
            Document.run != 2,
            or_(Document.status == 1, state.operation == "retract"),
            or_(state.source_ready.is_(True), state.operation == "retract"),
            state.status.in_(("pending", "retry_wait")),
            state.stage == "map_pending",
            state.batch_id.is_(None),
            state.task_id.is_not(None),
        )
        .order_by(state.updated_at, state.document_id)
    )


def claim(session, kb_id, limit):
    batch_id = uuid4()
    rows = session.scalars(
        eligible(kb_id).limit(limit).with_for_update(of=WikiDocumentState, skip_locked=True)
    )
    docs = []
    for row in rows:
        row.batch_id, row.claimed_at = batch_id, utcnow()
        docs.append({**state_delivery(row), "batch_id": str(batch_id)})
    return docs


def owned_round(session, doc):
    lock_knowledge(session, doc["kb_id"])
    _, row = lock_document(session, doc["kb_id"], doc["document_id"])
    if (
        row is None
        or str(row.batch_id) != doc["batch_id"]
        or row.task_id != doc["task_id"]
        or row.requested_version != doc["requested_version"]
    ):
        raise WikiSuperseded("WIKI_TASK_SUPERSEDED", retryable=False)
    return row


def _fail_doc(session, doc, error, settings):
    row = owned_round(session, doc)
    again = failures.map_failure(
        session, doc, error, settings, deferred=isinstance(error, WikiDeferred)
    )
    row.batch_id = row.claimed_at = None
    if again:
        row.stage = "map_pending"
    return again


def _record(runtime, doc, exc):
    if isinstance(exc, WikiSuperseded):
        return False
    error = exc if isinstance(exc, WikiError) else WikiError("WIKI_EXECUTION_FAILED")
    error.diagnostics = {**exception_details(exc), **doc, "error_code": error.code}
    logger.error("Wiki Map 文档失败 %s", diagnostic_json(error.diagnostics))
    try:
        return transaction(runtime, _fail_doc, doc, error, runtime.settings)
    except WikiSuperseded:
        return False


async def _read_batch(runtime, documents):
    slots = asyncio.Semaphore(runtime.settings.kb_wiki_batch_size)

    async def read(doc, snapshot):
        async with slots:
            try:
                return await execution.read_sources(
                    runtime,
                    doc["kb_id"],
                    doc["document_id"],
                    expect_index=bool(snapshot["chunk_count"]),
                )
            except Exception as exc:
                return exc

    values = await asyncio.gather(*(read(doc, snapshot) for doc, snapshot in documents))
    return {doc["document_id"]: value for (doc, _), value in zip(documents, values, strict=True)}


def _prepare(runtime, doc, snapshot, raw, ensure, ensure_batch, slots):
    if isinstance(raw, Exception):
        raise raw
    def ensure_owned():
        ensure_batch()
        ensure()

    with bind_usage_context("document", doc["document_id"]):
        work = execution.extract_document(runtime, snapshot, raw, ensure_owned, slots)
        work.pop("model", None)
        return work


def _plan_topics(runtime, items, context):
    # 批内用临时唯一句柄回填身份决定。各文档的 S/T 引用、摘要和证据仍保持原样。
    legacy_counts = Counter(topic_identity(p["kind"], p["name"]) for p in context["pages"])
    groups = {}
    model = None
    for doc, work in items:
        if not work["draft"]:
            continue
        snapshot = work["snapshot"]
        if snapshot["config_hash"] != context["config_hash"]:
            work["plan"] = WikiDeferred("WIKI_CONFIG_CHANGED")
            continue
        if model is None:
            model = execution.make_model(runtime, snapshot)
        try:
            with bind_usage_context("document", doc["document_id"]):
                topics = resolve_candidates(model, snapshot, work["draft"]["topics"])
                if (
                    model.settings.kb_wiki_max_topics
                    and len(topics) > model.settings.kb_wiki_max_topics
                ):
                    raise WikiError("WIKI_TOPIC_BUDGET_EXCEEDED", retryable=False)
                work["draft"]["topics"] = topics
        except WikiError as exc:
            work["plan"] = exc
            continue
        for topic in work["draft"]["topics"]:
            identity = topic_identity(topic["kind"], topic["name"])
            # 历史同名多页可能按原文档的旧 canonical_key 区分。
            document_key = doc["document_id"] if legacy_counts[identity] > 1 else None
            key = (
                identity,
                topic.get("semantic_type"),
                document_key,
                topic.get("scope"),
                tuple(sorted(topic.get("aliases", ()))),
            )
            groups.setdefault(key, []).append((doc, work, topic))
    if not groups:
        return
    representatives = []
    members = {}
    for index, originals in enumerate(groups.values(), start=1):
        handle = f"T{index}"
        doc, work, first = originals[0]
        representative = deepcopy(first)
        representative.update(topic_ref=handle, source_refs=[f"S{index}"], merged_topic_refs=[])
        try:
            # 保留按文档消歧的历史规则，相同候选在同一批只查询一次目录。
            with bind_usage_context("document", doc["document_id"]):
                reconcile(
                    model, work["snapshot"], [representative], context, defer_legacy_reuse=True
                )
        except WikiError as exc:
            for _, failed_work, _ in originals:
                failed_work["plan"] = exc
            continue
        representatives.append(representative)
        members[handle] = [topic for _, _, topic in originals]
    if not representatives:
        return
    snapshot = next(
        work["snapshot"]
        for _, work in items
        if work["draft"] and work["snapshot"]["config_hash"] == context["config_hash"]
    )
    reuse_qualified_legacy_pages(representatives, context["pages"])
    known = [t for t in representatives if t.get("existing_page_id")]
    novel = [t for t in representatives if not t.get("existing_page_id")]
    planned = known + resolve_candidates(model, snapshot, novel)
    unique, by_identity = [], {}
    for topic in planned:
        identity = (
            topic.get("existing_page_id"),
            *topic_identity(topic["kind"], topic["name"]),
            topic.get("semantic_type"),
        )
        if identity in by_identity:
            by_identity[identity]["merged_topic_refs"].extend(
                [topic["topic_ref"], *topic["merged_topic_refs"]]
            )
        else:
            by_identity[identity] = topic
            unique.append(topic)
    planned = taxonomy(model, snapshot, unique, context)
    for topic in planned:
        for ref in [topic["topic_ref"], *topic.get("merged_topic_refs", [])]:
            for original in members[ref]:
                for key in (
                    "name",
                    "title",
                    "kind",
                    "semantic_type",
                    "category_path",
                    "existing_page_id",
                    "disambiguated",
                    "correct_type",
                ):
                    if key in topic:
                        original[key] = topic[key]


def _register_plans(session, kb_id, items, revision):
    lock_knowledge(session, kb_id)
    if pages.planning_context(session, as_uuid(kb_id))["revision"] != revision:
        raise WikiDeferred("WIKI_TAXONOMY_CHANGED")
    result = {}
    for doc, work in items:
        try:
            if isinstance(work["plan"], Exception):
                raise work["plan"]
            with session.begin_nested():
                owned_round(session, doc)
                result[doc["document_id"]] = pages.fix_plan(
                    session,
                    doc,
                    work["draft"]["topics"] if work["draft"] else [],
                    work["input_hash"],
                    has_text=work["has_text"],
                    document_title=work["snapshot"]["title"],
                )
        except WikiError as exc:
            result[doc["document_id"]] = exc
    return result


def _commit_doc(session, doc, work):
    row = owned_round(session, doc)
    pending = execution.finish_map(session, doc, work)
    messages = deliveries.register(session, doc["kb_id"], pending)
    row.batch_id = row.claimed_at = None
    reruns = pending if work.get("snapshot", {}).get("explicit_page_rerun") else []
    return messages, reruns


def run_batch(runtime, kb_id):
    settings = runtime.settings
    with map_batch_lease(
        runtime.redis.sync_client(), kb_id, settings.kb_wiki_batch_max_workers_per_kb,
    ) as ensure_batch:
        if ensure_batch is None:
            logger.info("Wiki Map 已达同库并发上限 kb_id=%s", kb_id)
            return {"documents": 0}
        docs = transaction(runtime, claim, kb_id, settings.kb_wiki_batch_size)
        if not docs:
            logger.info("Wiki Map 无待领取文档 kb_id=%s", kb_id)
            return {"documents": 0}
        result, retry = _process_batch(runtime, kb_id, docs, ensure_batch)

    # 先释放批次名额，再查询待办，避免并发满额时跳过了最后一个新触发。
    with_pending = transaction(
        runtime,
        lambda s: (
            s.scalar(eligible(kb_id).with_only_columns(WikiDocumentState.document_id).limit(1))
            is not None
        ),
    )
    if with_pending:
        delay = (
            settings.kb_wiki_retry_delay_seconds
            if retry
            else settings.kb_wiki_batch_followup_seconds
        )
        wake_batch(runtime, kb_id, delay)
    return result


def _process_batch(runtime, kb_id, docs, ensure_batch):
    settings = runtime.settings
    logger.info("Wiki Map 已领取 kb_id=%s batch_id=%s documents=%d",
                kb_id, docs[0]["batch_id"], len(docs))
    ready, messages = [], []
    rerun_ids = set()
    failed, retry = 0, False
    slots = BoundedSemaphore(settings.kb_wiki_batch_size)
    with (
        ExitStack() as leases,
        ThreadPoolExecutor(max_workers=settings.kb_wiki_batch_size) as pool,
    ):
        to_extract = []
        for doc in docs:
            try:
                ensure = leases.enter_context(
                    map_lease(
                        runtime.redis.sync_client(),
                        kb_id,
                        doc["document_id"],
                        doc["requested_version"],
                    )
                )
                snapshot = transaction(runtime, pages.load_map, doc)
                if snapshot["mapped"]:
                    pending = transaction(runtime, execution._pending, doc)
                    ready.append((doc, ensure, {"pending": pending}))
                else:
                    to_extract.append((doc, ensure, snapshot))
            except Exception as exc:
                failed += 1
                retry |= _record(runtime, doc, exc)
        reads = [(doc, snapshot) for doc, _, snapshot in to_extract]
        sources = runtime.run_async(lambda: _read_batch(runtime, reads)) if reads else {}
        jobs = [
            (
                doc,
                ensure,
                pool.submit(
                    copy_context().run,
                    _prepare,
                    runtime,
                    doc,
                    snapshot,
                    sources.pop(doc["document_id"]),
                    ensure,
                    ensure_batch,
                    slots,
                ),
            )
            for doc, ensure, snapshot in to_extract
        ]
        for doc, ensure, future in jobs:
            try:
                ready.append((doc, ensure, future.result()))
            except Exception as exc:
                failed += 1
                retry |= _record(runtime, doc, exc)
        planning = [
            (doc, work) for doc, _, work in ready if "plan" in work and work["plan"] is None
        ]
        if planning:
            try:
                # 同库批次可以并行提取，等待目录轮到本批时复用已提取的素材。
                with taxonomy_lease(
                    runtime.redis.sync_client(), kb_id,
                    wait_seconds=settings.kb_task_soft_time_limit_seconds,
                ) as ensure_directory:
                    ensure_batch()
                    context = transaction(runtime, pages.load_batch_context, kb_id)
                    _plan_topics(runtime, planning, context)
                    ensure_batch()
                    ensure_directory()
                    plans = transaction(
                        runtime, _register_plans, kb_id, planning, context["revision"]
                    )
            except Exception as exc:
                plans = {doc["document_id"]: exc for doc, _ in planning}
            for doc, work in planning:
                work["plan"] = plans[doc["document_id"]]
        reads = [
            (doc, work["snapshot"])
            for doc, _, work in ready
            if "plan" in work and not isinstance(work["plan"], Exception)
        ]
        sources = runtime.run_async(lambda: _read_batch(runtime, reads)) if reads else {}
        for doc, ensure, work in ready:
            try:
                if isinstance(work.get("plan"), Exception):
                    raise work["plan"]
                if "plan" in work:
                    current = sources.pop(doc["document_id"])
                    if isinstance(current, Exception):
                        raise current
                    work["materials"] = execution.prepare_materials(work, current)
                ensure_batch()
                ensure()
                new_messages, reruns = transaction(runtime, _commit_doc, doc, work)
                messages.extend(new_messages)
                rerun_ids.update(reruns)
            except Exception as exc:
                failed += 1
                retry |= _record(runtime, doc, exc)
    from .delivery import rerun_page

    for page_id in sorted(rerun_ids - {msg["page_id"] for msg in messages}):
        try:
            rerun_page(runtime, kb_id, page_id)
        except WikiDeferred as exc:
            if exc.code != "WIKI_PAGE_BUSY":
                raise
    execution.publish_pages(runtime, kb_id, messages)
    logger.info("Wiki Map 已交接 kb_id=%s batch_id=%s documents=%d failed=%d page_messages=%d",
                kb_id, docs[0]["batch_id"], len(docs), failed, len(messages))
    return {"documents": len(docs), "failed": failed, "page_messages": len(messages)}, retry
