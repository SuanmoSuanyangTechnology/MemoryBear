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
    try:
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
            dispatcher(runtime).send_sync(
                REDUCE_TASK,
                kwargs={"kb_id": str(kb_id), "page_id": str(page_id)},
                queue=WIKI_QUEUE,
            )
        except Exception as exc:
            raise WikiError("WIKI_REDUCE_DISPATCH_FAILED") from exc


def _pending(session, delivery):
    lock_knowledge(session, delivery["kb_id"])
    _, current = lock_document(session, delivery["kb_id"], delivery["document_id"])
    if current is None or current.requested_version != delivery["requested_version"]:
        return []
    return pages.pending_page_ids(session, current)


def _plan_topics(runtime, model, snapshot, draft, delivery, input_hash):
    # 模型调用不持有数据库事务；租约保护目录快照到页面计划注册的完整阶段。
    with taxonomy_lease(runtime.redis.sync_client(), delivery["kb_id"]) as ensure_owned:
        context = transaction(runtime, pages.load_planning_context, delivery)
        candidates = taxonomy(
            model, snapshot, reconcile(model, snapshot, draft["topics"], context), context
        )
        ensure_owned()
        return transaction(
            runtime, pages.fix_plan, delivery, candidates, input_hash,
            has_text=True, document_title=snapshot["title"],
            planning_revision=context["revision"],
        )


def execute_map(runtime, delivery):
    snapshot = transaction(
        runtime, pages.load_map, delivery, failure_limit=runtime.settings.kb_wiki_failure_attempts
    )
    if snapshot["mapped"]:
        pending = transaction(runtime, _pending, delivery)
    else:
        raw = runtime.run_async(
            lambda: read_sources(
                runtime,
                delivery["kb_id"],
                delivery["document_id"],
                expect_index=bool(snapshot["chunk_count"]),
            )
        )
        segments, catalog, _ = segment_sources(raw, runtime.settings, snapshot)
        input_hash = fingerprint({"title": snapshot["title"], "sources": raw})
        if snapshot.get("reuse_candidate") and snapshot["input_hash"] == input_hash:
            pending = transaction(
                runtime,
                pages.confirm_reused_map,
                delivery,
                input_hash,
                snapshot["config_hash"],
                document_title=snapshot["title"],
            )
        else:
            model = make_model(runtime, snapshot) if segments else None
            if snapshot["plan_ready"] and snapshot["input_hash"] == input_hash:
                plan = snapshot["plan"]
                draft = prepare_document(model, snapshot, plan["topics"], segments, catalog,
                                         fixed_plan=True) if segments else None
            else:
                candidates = extract_candidates(model, snapshot, segments) if segments else []
                draft = prepare_document(model, snapshot, candidates, segments, catalog) if (
                    segments
                ) else None
                if draft:
                    plan = _plan_topics(runtime, model, snapshot, draft, delivery, input_hash)
                else:
                    plan = transaction(
                        runtime, pages.fix_plan, delivery, candidates, input_hash,
                        has_text=bool(segments), document_title=snapshot["title"],
                    )
            materials = (
                bind_materials(draft, plan, catalog, snapshot["title"]) if draft else []
            )
            # Re-check source content after model calls without keeping a DB connection open.
            current = runtime.run_async(
                lambda: read_sources(
                    runtime,
                    delivery["kb_id"],
                    delivery["document_id"],
                    expect_index=bool(snapshot["chunk_count"]),
                )
            )
            if fingerprint({"title": snapshot["title"], "sources": current}) != input_hash:
                raise WikiDeferred("WIKI_SOURCE_CHANGED")
            pending = transaction(
                runtime,
                pages.commit_map,
                delivery,
                config_hash=snapshot["config_hash"],
                input_hash=input_hash,
                materials=materials,
                document_title=snapshot["title"],
            )
    publish_pages(runtime, delivery["kb_id"], pending)
    return {"document_id": delivery["document_id"], "pages_dispatched": len(pending)}


def execute_reduce(runtime, kb_id, page_id, *, redelivered=False):
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
