"""Map hands off page messages; Reduce publishes independently under a page lease."""

from ...rag.wiki.config import (
    MAP_TASK,
    REDUCE_TASK,
    WIKI_QUEUE,
    WikiDeferred,
    WikiError,
    fingerprint,
)
from ...rag.wiki.document import bind_materials, prepare_document
from ...rag.wiki.input import read_sources, segment_sources
from ...rag.wiki.pipeline import extract_candidates, generate_page
from ...rag.wiki.planning import reconcile, taxonomy
from ...rag.wiki.resolution import resolve_candidates
from ...repositories.wiki import pages, state
from ...repositories.wiki.common import lock_document, lock_knowledge
from ...tasks.dispatch import TaskDispatcher
from .lease import page_lease, taxonomy_lease
from .runtime import make_model, transaction


def dispatcher(runtime):
    return getattr(runtime, "task_dispatcher", None) or TaskDispatcher(runtime=runtime)


def publish_map(runtime, delivery, *, terminal_on_failure=False):
    if not delivery or not delivery.get("publish", True):
        return
    from .batch import batch_enabled, wake_batch

    try:
        if batch_enabled(runtime.settings, delivery["kb_id"]):
            wake_batch(runtime, delivery["kb_id"])
        else:
            dispatcher(runtime).send_sync(
                MAP_TASK,
                kwargs={
                    key: delivery[key]
                    for key in ("kb_id", "document_id", "requested_version", "operation")
                },
                queue=WIKI_QUEUE,
                task_id=delivery["task_id"],
            )
    except Exception:
        transaction(runtime, state.record_publish_failure, delivery, terminal=terminal_on_failure)
        raise
    transaction(runtime, state.confirm_publish, delivery)


def publish_pages(runtime, kb_id, page_ids):
    for page_id in page_ids:
        try:
            if isinstance(page_id, dict):
                from .delivery import send_page

                send_page(runtime, page_id)
                continue
            dispatcher(runtime).send_sync(
                REDUCE_TASK,
                kwargs={"kb_id": str(kb_id), "page_id": str(page_id)},
                queue=WIKI_QUEUE,
            )
        except Exception as exc:
            target = page_id if isinstance(page_id, dict) else {"page_id": str(page_id)}
            raise WikiError("WIKI_REDUCE_DISPATCH_FAILED", diagnostics={
                "stage": "reduce_dispatch", "kb_id": str(kb_id), **target,
            }) from exc


def _pending(session, delivery):
    lock_knowledge(session, delivery["kb_id"])
    _, current = lock_document(session, delivery["kb_id"], delivery["document_id"])
    if current is None or current.requested_version != delivery["requested_version"]:
        return []
    return pages.pending_page_ids(session, current)


def _plan_topics(runtime, model, snapshot, draft, delivery, input_hash, ensure_map_owned):
    # 模型调用不持有数据库事务；租约保护目录快照到页面计划注册的完整阶段。
    ensure_map_owned()
    with taxonomy_lease(runtime.redis.sync_client(), delivery["kb_id"]) as ensure_owned:
        context = transaction(runtime, pages.load_planning_context, delivery)
        draft["topics"] = resolve_candidates(model, snapshot, draft["topics"])
        limit = model.settings.kb_wiki_max_topics
        if limit and len(draft["topics"]) > limit:
            raise WikiError("WIKI_TOPIC_BUDGET_EXCEEDED", retryable=False)
        candidates = reconcile(model, snapshot, draft["topics"], context)
        ensure_map_owned()
        candidates = taxonomy(model, snapshot, candidates, context)
        ensure_owned()
        ensure_map_owned()
        return transaction(
            runtime,
            pages.fix_plan,
            delivery,
            candidates,
            input_hash,
            has_text=True,
            document_title=snapshot["title"],
            planning_revision=context["revision"],
        )


def prepare_map(runtime, delivery, ensure_owned):
    ensure_owned()
    snapshot = transaction(
        runtime, pages.load_map, delivery, failure_limit=runtime.settings.kb_wiki_failure_attempts
    )
    if snapshot["mapped"]:
        return {"pending": transaction(runtime, _pending, delivery)}
    raw = load_sources(runtime, delivery, snapshot)
    return extract_document(runtime, snapshot, raw, ensure_owned)


def extract_document(runtime, snapshot, raw, ensure_owned, request_slots=None):
    segments, catalog, _ = segment_sources(raw, runtime.settings, snapshot)
    input_hash = fingerprint({"title": snapshot["title"], "sources": raw})
    ensure_owned()
    if snapshot.get("reuse_candidate") and snapshot["input_hash"] == input_hash:
        return {"reuse": True, "snapshot": snapshot, "input_hash": input_hash}
    model = make_model(runtime, snapshot) if segments else None
    if model is not None and request_slots is not None:
        model.request_slots = request_slots
    fixed = snapshot["plan_ready"] and snapshot["input_hash"] == input_hash
    plan = snapshot["plan"] if fixed else None
    draft = None
    if segments:
        topics = plan["topics"] if fixed else extract_candidates(model, snapshot, segments)
        ensure_owned()
        draft = prepare_document(model, snapshot, topics, segments, catalog, fixed_plan=bool(fixed))
    return {
        "snapshot": snapshot,
        "input_hash": input_hash,
        "plan": plan,
        "draft": draft,
        "catalog": catalog,
        "has_text": bool(segments),
        "model": model,
    }


def load_sources(runtime, delivery, snapshot):
    return runtime.run_async(
        lambda: read_sources(
            runtime,
            delivery["kb_id"],
            delivery["document_id"],
            expect_index=bool(snapshot["chunk_count"]),
        )
    )


def prepare_materials(work, current):
    draft, snapshot = work["draft"], work["snapshot"]
    materials = (
        bind_materials(draft, work["plan"], work["catalog"], snapshot["title"]) if draft else []
    )
    if fingerprint({"title": snapshot["title"], "sources": current}) != work["input_hash"]:
        raise WikiDeferred("WIKI_SOURCE_CHANGED")
    return materials


def finish_map(session, delivery, work):
    if "pending" in work:
        return work["pending"]
    snapshot = work["snapshot"]
    if work.get("reuse"):
        return pages.confirm_reused_map(
            session,
            delivery,
            work["input_hash"],
            snapshot["config_hash"],
            document_title=snapshot["title"],
        )
    return pages.commit_map(
        session,
        delivery,
        config_hash=snapshot["config_hash"],
        input_hash=work["input_hash"],
        materials=work["materials"],
        document_title=snapshot["title"],
        source_catalog=work["catalog"],
    )


def execute_map(runtime, delivery, *, ensure_owned):
    work = prepare_map(runtime, delivery, ensure_owned)
    if "pending" not in work and not work.get("reuse"):
        snapshot, draft = work["snapshot"], work["draft"]
        if work["plan"] is None:
            if draft:
                work["plan"] = _plan_topics(
                    runtime,
                    work["model"],
                    snapshot,
                    draft,
                    delivery,
                    work["input_hash"],
                    ensure_owned,
                )
            else:
                work["plan"] = transaction(
                    runtime,
                    pages.fix_plan,
                    delivery,
                    [],
                    work["input_hash"],
                    has_text=work["has_text"],
                    document_title=snapshot["title"],
                )
        work["materials"] = prepare_materials(work, load_sources(runtime, delivery, snapshot))
    ensure_owned()
    pending = transaction(runtime, finish_map, delivery, work)
    for page_id in pending:
        ensure_owned()
        publish_pages(runtime, delivery["kb_id"], [page_id])
    return {"document_id": delivery["document_id"], "pages_dispatched": len(pending)}


def execute_reduce(runtime, kb_id, page_id, *, redelivered=False):
    from ..wiki_faq import publish_pending

    result = _execute_reduce(runtime, kb_id, page_id, redelivered=redelivered)
    publish_pending(runtime, kb_id, page_id)
    return result


def _execute_reduce(runtime, kb_id, page_id, *, redelivered=False):
    snapshot = None
    try:
        with page_lease(runtime.redis.sync_client(), kb_id, page_id) as ensure_owned:
            snapshot = transaction(
                runtime,
                pages.load_reduce,
                kb_id,
                page_id,
                redelivered=redelivered,
                failure_limit=runtime.settings.kb_wiki_failure_attempts,
            )
            if snapshot is None:
                return None
            model = (
                make_model(runtime, snapshot)
                if (snapshot["sources"] and snapshot["page_type"] != "summary")
                else None
            )
            result = generate_page(model, snapshot)
            ensure_owned()
            return transaction(runtime, pages.commit_reduce, snapshot, result)
    except Exception as exc:
        # The failure handler uses this version only for fencing, never model input/output.
        if snapshot is not None:
            exc.wiki_snapshot = snapshot
        raise
