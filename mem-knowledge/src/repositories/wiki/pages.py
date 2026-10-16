"""Stable page identities and atomic Map/Reduce publication."""

from __future__ import annotations

from sqlalchemy import delete, select, update

from ...models.owned import (
    WikiDocumentPageTask,
    WikiFolder,
    WikiPage,
    WikiPageLink,
    WikiPageSource,
)
from ...rag.wiki.config import (
    TERMINAL_TASK_STATUSES,
    TYPE_GROUP_NAMES,
    WikiDeferred,
    WikiError,
    WikiSuperseded,
    fingerprint,
    generation_config_hash,
    normalize_name,
    read_wiki_config,
)
from .common import (
    append_log,
    as_uuid,
    current_tasks,
    lock_document,
    lock_documents,
    lock_knowledge,
    require_round,
    settle_progress,
)
from .state import affected_page_ids, dirty_pages


def load_map(session, delivery, *, failure_limit=None):
    knowledge, scope = lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    require_round(
        document,
        state,
        delivery["requested_version"],
        allow_inactive=delivery["operation"] == "retract",
    )
    if state.task_id != delivery["task_id"]:
        raise WikiSuperseded("WIKI_TASK_SUPERSEDED", retryable=False)
    if state.operation != delivery["operation"]:
        raise WikiSuperseded("WIKI_OPERATION_SUPERSEDED", retryable=False)
    if state.status in {"ready", "skipped", "failed"}:
        raise WikiSuperseded("WIKI_ROUND_ALREADY_TERMINAL", retryable=False)
    if delivery.get("redelivered") and state.status == "running":
        state.failure_attempt += 1
        if failure_limit is not None and state.failure_attempt >= failure_limit:
            raise WikiError("WIKI_WORKER_LOSS_EXHAUSTED", retryable=False)
    if not state.source_ready and state.operation != "retract":
        raise WikiDeferred("WIKI_SOURCE_NOT_READY")
    config_hash = generation_config_hash(knowledge.llm_id, knowledge.parser_config)
    if state.operation != "retract" and state.config_hash and state.config_hash != config_hash:
        raise WikiError("WIKI_CONFIG_CHANGED", retryable=False)
    state.config_hash = config_hash
    state.status = "running"
    state.publish_confirmed = True
    document.run = 1
    document.progress = max(0.0, state.last_progress)
    config = read_wiki_config(knowledge.parser_config)
    return {
        **{k: str(v) for k, v in scope.items()},
        "document_id": str(document.id),
        "chunk_count": document.chunk_num,
        "title": document.file_name,
        "llm_id": str(knowledge.llm_id),
        "parser_config": dict(document.parser_config or {}),
        "language": config.language,
        "extract_prompt": config.extract_prompt,
        "config_hash": config_hash,
        "input_hash": state.input_hash,
        "plan_ready": state.plan_ready,
        "plan": load_plan(session, state),
        "reuse_candidate": bool(state.input_hash and not state.plan_ready),
        "mapped": state.mapped_version == state.requested_version,
        "requested_version": state.requested_version,
    }


def load_plan(session, state):
    """Rebuild stable handles from authoritative task rows, never progress JSON."""
    tasks = current_tasks(session, state)
    upserts = {task.page_id for task in tasks if task.operation == "upsert"}
    topics, summary_id = [], None
    for page in session.scalars(
        select(WikiPage)
        .where(WikiPage.kb_id == state.kb_id, WikiPage.id.in_(upserts))
        .order_by(WikiPage.id)
    ):
        if page.page_type == "summary":
            summary_id = str(page.id)
        else:
            topics.append(
                {
                    "topic_ref": f"T{len(topics) + 1}",
                    "page_id": str(page.id),
                    "kind": page.page_type,
                    "title": page.title,
                    "name": page.title,
                    "aliases": page.aliases,
                    "scope": page.topic_scope,
                }
            )
    return {"topics": topics, "summary_id": summary_id, "total": len(tasks)}


def _folder(session, scope, path):
    parent_id = None
    for name in path[:2]:
        normalized = normalize_name(name)
        if normalized in TYPE_GROUP_NAMES:
            continue
        folder = session.scalar(
            select(WikiFolder).where(
                WikiFolder.kb_id == scope["kb_id"],
                WikiFolder.parent_id == parent_id,
                WikiFolder.normalized_name == normalized,
            )
        )
        if folder is None:
            folder = WikiFolder(**scope, parent_id=parent_id, name=name, normalized_name=normalized)
            session.add(folder)
            session.flush()
        parent_id = folder.id
    return parent_id


def _assign_missing_folder(session, scope, page, candidate):
    if page.folder_id is None:
        if "category_path" not in candidate:
            raise WikiDeferred("WIKI_TAXONOMY_CHANGED", diagnostics={
                "stage": "taxonomy_register", "knowledge_id": str(scope["kb_id"]),
                "page_id": str(page.id),
                "error_message": "页面目录在规划后被移除，当前候选缺少分类结果，需要重新规划",
            })
        page.folder_id = _folder(session, scope, candidate["category_path"])
    return page


def _page_for_candidate(session, scope, candidate, document_id):
    name = normalize_name(candidate["name"])
    topic_scope = normalize_name(candidate["scope"])
    names = {name, *(normalize_name(v) for v in candidate["aliases"])}
    if candidate.get("existing_page_id"):
        page = session.scalar(select(WikiPage).where(
            WikiPage.id == as_uuid(candidate["existing_page_id"]),
            WikiPage.kb_id == scope["kb_id"], WikiPage.page_type == candidate["kind"],
        ))
        if page is None:
            raise WikiDeferred("WIKI_IDENTITY_CHANGED")
        return _assign_missing_folder(session, scope, page, candidate)
    existing = list(
        session.scalars(
            select(WikiPage).where(
                WikiPage.kb_id == scope["kb_id"],
                WikiPage.page_type == candidate["kind"],
            )
        )
    )
    matches = [
        page
        for page in existing
        if normalize_name(page.topic_scope) == topic_scope
        and names.intersection({page.normalized_name, *(normalize_name(v) for v in page.aliases)})
    ]
    if len(matches) == 1:
        return _assign_missing_folder(session, scope, matches[0], candidate)
    key = fingerprint([candidate["kind"], name, topic_scope or str(document_id)])
    page = session.scalar(
        select(WikiPage).where(WikiPage.kb_id == scope["kb_id"], WikiPage.canonical_key == key)
    )
    if page is None:
        page = WikiPage(
            **scope,
            canonical_key=key,
            title=candidate["name"],
            normalized_name=name,
            topic_scope=candidate["scope"],
            page_type=candidate["kind"],
            aliases=candidate["aliases"],
            folder_id=_folder(session, scope, candidate["category_path"]),
        )
        session.add(page)
        session.flush()
    return _assign_missing_folder(session, scope, page, candidate)


def fix_plan(
    session, delivery, candidates, input_hash, *, has_text, document_title,
    planning_revision=None,
):
    knowledge, scope = lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    require_round(document, state, delivery["requested_version"])
    if state.plan_ready:
        if state.input_hash != input_hash:
            raise WikiError("WIKI_SOURCE_CHANGED", retryable=False)
        plan = load_plan(session, state)
        mapping = {}
        for candidate in candidates:
            names = {normalize_name(candidate["name"]),
                     *(normalize_name(alias) for alias in candidate["aliases"])}
            matches = [topic for topic in plan["topics"] if (
                topic["page_id"] == candidate.get("existing_page_id") or (
                    topic["kind"] == candidate["kind"]
                    and normalize_name(topic["scope"]) == normalize_name(candidate["scope"])
                    and names & {normalize_name(topic["title"]),
                                 *(normalize_name(alias) for alias in topic["aliases"])}
                )
            )]
            if len(matches) != 1:
                raise WikiDeferred("WIKI_PLAN_CHANGED")
            if candidate.get("topic_ref"):
                mapping[candidate["topic_ref"]] = matches[0]["page_id"]
        return {**plan, "topic_pages": mapping}
    if document.file_name != document_title:
        raise WikiDeferred("WIKI_SOURCE_CHANGED")
    if state.config_hash != generation_config_hash(knowledge.llm_id, knowledge.parser_config):
        raise WikiDeferred("WIKI_CONFIG_CHANGED")
    if state.mapped_version == state.requested_version:
        raise WikiSuperseded("WIKI_MAP_ALREADY_COMMITTED", retryable=False)
    if planning_revision is not None:
        current_revision = _planning_context(session, knowledge.id)["revision"]
        if current_revision != planning_revision:
            raise WikiDeferred("WIKI_TAXONOMY_CHANGED", diagnostics={
                "stage": "taxonomy_register", "knowledge_id": str(knowledge.id),
                "document_id": delivery["document_id"], "expected_revision": planning_revision,
                "current_revision": current_revision,
                "error_message": "模型规划期间目录结构或页面归属已变化，需要重新读取目录规划",
            })
    topics, topic_pages = [], {}
    new_ids = set()
    if has_text:
        summary_key = f"summary:{document.id}"
        summary = session.scalar(
            select(WikiPage).where(
                WikiPage.kb_id == knowledge.id, WikiPage.canonical_key == summary_key
            )
        )
        if summary is None:
            summary = WikiPage(
                **scope,
                canonical_key=summary_key,
                title=document.file_name,
                normalized_name=fingerprint(document.file_name),
                page_type="summary",
            )
            session.add(summary)
            session.flush()
        else:
            summary.title = document.file_name
            summary.normalized_name = fingerprint(document.file_name)
        new_ids.add(summary.id)
        for candidate in candidates:
            page = _page_for_candidate(session, scope, candidate, document.id)
            if candidate.get("topic_ref"):
                topic_pages[candidate["topic_ref"]] = str(page.id)
            if page.id in new_ids:
                continue
            new_ids.add(page.id)
            topics.append(
                {
                    "topic_ref": f"T{len(topics) + 1}",
                    "page_id": str(page.id),
                    "title": page.title,
                    "kind": page.page_type,
                    "scope": page.topic_scope,
                }
            )
    old_ids = affected_page_ids(session, document.id)
    targets = old_ids | new_ids
    session.execute(
        delete(WikiDocumentPageTask).where(
            WikiDocumentPageTask.document_id == document.id,
            WikiDocumentPageTask.requested_version == state.requested_version,
        )
    )
    for page_id in sorted(targets, key=str):
        session.add(
            WikiDocumentPageTask(
                **scope,
                document_id=document.id,
                requested_version=state.requested_version,
                page_id=page_id,
                operation="upsert" if page_id in new_ids else "retract",
            )
        )
    state.map_progress = {"candidate_count": len(topics), "planned_pages": len(targets)}
    state.plan_ready, state.input_hash = True, input_hash
    state.stage = "materials"
    append_log(document, f"Wiki page plan ready: pages={len(targets)}.")
    session.flush()
    return {**load_plan(session, state), "topic_pages": topic_pages}


def load_planning_context(session, delivery):
    knowledge, _ = lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    require_round(document, state, delivery["requested_version"])
    return _planning_context(session, knowledge.id)


def _planning_context(session, kb_id):
    pages = [{"page_id": str(row.id), "name": row.title, "kind": row.page_type,
              "scope": row.topic_scope, "aliases": row.aliases,
              "description": row.summary, "status": row.status,
              "folder_id": str(row.folder_id) if row.folder_id else None}
             for row in session.execute(select(
                 WikiPage.id, WikiPage.title, WikiPage.page_type, WikiPage.topic_scope,
                 WikiPage.aliases, WikiPage.folder_id, WikiPage.summary, WikiPage.status,
             ).where(WikiPage.kb_id == kb_id, WikiPage.page_type != "summary")
             .order_by(WikiPage.id))]
    folders = {folder.id: folder for folder in session.scalars(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id).order_by(WikiFolder.id)
    )}
    paths, by_id = [], {}
    for folder in folders.values():
        parent = folders.get(folder.parent_id)
        path = [parent.name, folder.name] if parent else [folder.name]
        paths.append(path)
        by_id[str(folder.id)] = path
    for page in pages:
        page["folder_path"] = by_id.get(page["folder_id"], [])
    # 正文发布和状态推进不应使分类失效；只校验身份、目录归属及目录结构。
    revision = fingerprint({
        "pages": [{key: page[key] for key in (
            "page_id", "name", "kind", "scope", "aliases", "folder_id"
        )} for page in pages],
        "folders": [
            [str(folder.id), str(folder.parent_id), folder.name] for folder in folders.values()
        ],
    })
    return {"pages": pages, "folders": sorted(paths), "revision": revision}


def commit_map(session, delivery, *, config_hash, input_hash, materials, document_title):
    knowledge, scope = lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    require_round(document, state, delivery["requested_version"])
    if state.mapped_version == state.requested_version:
        return pending_page_ids(session, state)
    if document.file_name != document_title:
        raise WikiError("WIKI_SOURCE_CHANGED", retryable=False)
    if (
        not state.plan_ready
        or not state.source_ready
        or state.input_hash != input_hash
        or state.config_hash != config_hash
        or config_hash != generation_config_hash(knowledge.llm_id, knowledge.parser_config)
    ):
        raise WikiDeferred("WIKI_INPUT_CHANGED")
    tasks = current_tasks(session, state)
    expected = {str(t.page_id) for t in tasks if t.operation == "upsert"}
    if {item["page_id"] for item in materials} != expected:
        raise WikiSuperseded("WIKI_MATERIAL_PLAN_MISMATCH", retryable=False)
    session.execute(delete(WikiPageSource).where(WikiPageSource.document_id == document.id))
    metadata_by_page = {item["page_id"]: item.get("source_context", {}).get("page_metadata", {})
                        for item in materials}
    for page in session.scalars(select(WikiPage).where(
        WikiPage.kb_id == knowledge.id, WikiPage.id.in_([as_uuid(p) for p in expected]),
    ).order_by(WikiPage.id).with_for_update()):
        metadata = metadata_by_page[str(page.id)]
        page.aliases = sorted(set([*page.aliases, *metadata.get("aliases", [])]))
        if page.folder_id is None and metadata.get("category_path"):
            page.folder_id = _folder(session, scope, metadata["category_path"])
    for item in materials:
        session.add(
            WikiPageSource(
                **scope,
                document_id=document.id,
                page_id=as_uuid(item["page_id"]),
                document_wiki_version=state.requested_version,
                content_hash=fingerprint(item),
                facts=item["facts"],
                chunk_refs=item["chunk_refs"],
                source_context=item["source_context"],
            )
        )
    dirty_pages(session, {t.page_id for t in tasks})
    state.mapped_version = state.requested_version
    state.stage = "reducing"
    append_log(document, "Wiki Map committed; page generation pending.")
    session.flush()
    settle_progress(session, document, state)
    return pending_page_ids(session, state)


def pending_page_ids(session, state):
    return [
        str(t.page_id)
        for t in current_tasks(session, state)
        if t.status not in TERMINAL_TASK_STATUSES
    ]


def confirm_reused_map(session, delivery, input_hash, config_hash, *, document_title):
    knowledge, _ = lock_knowledge(session, delivery["kb_id"])
    document, state = lock_document(session, delivery["kb_id"], delivery["document_id"])
    require_round(document, state, delivery["requested_version"])
    if state.mapped_version == state.requested_version:
        return pending_page_ids(session, state)
    if document.file_name != document_title:
        raise WikiDeferred("WIKI_SOURCE_CHANGED")
    if (
        state.input_hash != input_hash
        or state.config_hash != config_hash
        or config_hash != generation_config_hash(knowledge.llm_id, knowledge.parser_config)
    ):
        raise WikiDeferred("WIKI_INPUT_CHANGED")
    state.plan_ready = True
    state.mapped_version = state.requested_version
    state.stage = "reducing"
    state.map_progress = {"reused": True}
    append_log(document, "Wiki Map reused after source and configuration verification.")
    session.flush()
    settle_progress(session, document, state)
    return pending_page_ids(session, state)


def load_reduce(session, kb_id, page_id, *, redelivered=False, failure_limit=None):
    knowledge, scope = lock_knowledge(session, kb_id)
    page_id = as_uuid(page_id)
    sources = list(
        session.scalars(
            select(WikiPageSource)
            .where(
                WikiPageSource.kb_id == knowledge.id,
                WikiPageSource.page_id == page_id,
            )
            .order_by(WikiPageSource.document_id)
        )
    )
    tasks = list(
        session.scalars(
            select(WikiDocumentPageTask)
            .where(
                WikiDocumentPageTask.kb_id == knowledge.id,
                WikiDocumentPageTask.page_id == page_id,
                WikiDocumentPageTask.status.not_in(TERMINAL_TASK_STATUSES),
            )
            .order_by(WikiDocumentPageTask.document_id)
        )
    )
    ids = {s.document_id for s in sources} | {t.document_id for t in tasks}
    documents, states = lock_documents(session, kb_id, ids)
    page = session.scalar(
        select(WikiPage)
        .where(WikiPage.id == page_id, WikiPage.kb_id == knowledge.id)
        .with_for_update()
    )
    if page is None or (not tasks and page.status != "cleanup_pending"):
        return None
    if redelivered:
        page.failure_attempt += 1
        if failure_limit is not None and page.failure_attempt >= failure_limit:
            error = WikiError("WIKI_WORKER_LOSS_EXHAUSTED", retryable=False)
            error.wiki_snapshot = {"row_version": page.row_version}
            raise error
    config_hash = generation_config_hash(knowledge.llm_id, knowledge.parser_config)

    def invalid_source(code):
        error = WikiError(code, retryable=False)
        error.wiki_snapshot = {"row_version": page.row_version}
        return error

    valid_sources = []
    summary_contexts = {row.document_id: row.source_context for row in session.execute(
        select(WikiPageSource.document_id, WikiPageSource.source_context)
        .join(WikiPage, WikiPage.id == WikiPageSource.page_id)
        .where(WikiPageSource.kb_id == knowledge.id, WikiPage.page_type == "summary",
               WikiPageSource.document_id.in_({source.document_id for source in sources}))
    )}
    for source in sources:
        document, state = documents[source.document_id], states[source.document_id]
        if (
            document.status != 1
            or state is None
            or not state.source_ready
            or state.mapped_version != state.requested_version
            or source.document_wiki_version != state.requested_version
        ):
            if state and state.status in {"failed", "cancelled"}:
                raise invalid_source("WIKI_SOURCE_FAILED")
            raise WikiDeferred("WIKI_SOURCE_UPDATING")
        valid_sources.append(
            {
                "document_id": str(document.id),
                "title": document.file_name,
                "version": source.document_wiki_version,
                "content_hash": source.content_hash,
                "facts": source.facts,
                "chunk_refs": source.chunk_refs,
                "source_context": {**source.source_context,
                                   "summary": summary_contexts.get(source.document_id, {}).get(
                                       "summary", source.source_context.get("summary", {}))},
            }
        )
    participants = []
    known_sources = {row.document_id for row in sources}
    known_sources.update(as_uuid(row["document_id"]) for row in page.published_sources)
    for task in tasks:
        state = states[task.document_id]
        document = documents[task.document_id]
        if not state or task.requested_version != state.requested_version:
            task.status = "superseded"
            continue
        if state.status == "cancelled" or document.run == 2:
            task.status = "cancelled"
            continue
        if not state.plan_ready or state.mapped_version != state.requested_version:
            if task.document_id not in known_sources:
                continue
            if state.status == "failed":
                raise invalid_source("WIKI_SOURCE_FAILED")
            raise WikiDeferred("WIKI_SOURCE_UPDATING")
        if state.operation == "upsert" and state.config_hash != config_hash:
            raise invalid_source("WIKI_CONFIG_CHANGED")
        participants.append(
            {
                "document_id": str(task.document_id),
                "requested_version": task.requested_version,
                "subtask_id": str(task.subtask_id),
            }
        )
    if not participants and valid_sources:
        current_materials = {(s["document_id"], s["content_hash"]) for s in valid_sources}
        published_materials = {
            (s["document_id"], s["content_hash"]) for s in page.published_sources
        }
        if page.version > 0 and current_materials == published_materials:
            # Cancellation may leave an unchanged, already published page fully supported.
            page.status, page.error_code = "ready", None
            page.row_version += 1
            page.defer_started_at = None
            page.published_sources = [
                {
                    "document_id": s["document_id"],
                    "version": s["version"],
                    "content_hash": s["content_hash"],
                }
                for s in valid_sources
            ]
            return None
        if all(states[as_uuid(s["document_id"])].status == "cancelled" for s in valid_sources):
            page.status, page.error_code = "failed", "WIKI_CANCELLED"
            page.row_version += 1
            return None
    page.row_version += 1
    links = [
        {"page_id": str(p.id), "title": p.title, "type": p.page_type}
        for p in session.scalars(
            select(WikiPage)
            .where(
                WikiPage.kb_id == knowledge.id,
                WikiPage.id != page_id,
                WikiPage.id.in_(
                    select(WikiPageSource.page_id).where(
                        WikiPageSource.document_id.in_(ids),
                        WikiPageSource.kb_id == knowledge.id,
                    )
                ),
                WikiPage.status.in_(["ready", "updating", "pending"]),
            )
            .order_by(WikiPage.id)
        )
    ]
    config = read_wiki_config(knowledge.parser_config)
    return {
        **{k: str(v) for k, v in scope.items()},
        "page_id": str(page.id),
        "title": page.title,
        "page_type": page.page_type,
        "aliases": page.aliases,
        "row_version": page.row_version,
        "content": page.content,
        "llm_id": str(knowledge.llm_id),
        "language": config.language,
        "config_hash": generation_config_hash(knowledge.llm_id, knowledge.parser_config),
        "sources": valid_sources,
        "participants": participants,
        "allowed_links": links,
    }


def commit_reduce(session, snapshot, result):
    kb_id, page_id = snapshot["kb_id"], as_uuid(snapshot["page_id"])
    knowledge, scope = lock_knowledge(session, kb_id)
    ids = {p["document_id"] for p in snapshot["participants"]}
    ids.update(s["document_id"] for s in snapshot["sources"])
    locked_documents, locked_states = lock_documents(session, kb_id, ids)
    documents = {str(key): value for key, value in locked_documents.items()}
    states = {str(key): value for key, value in locked_states.items()}
    page = session.scalar(
        select(WikiPage)
        .where(WikiPage.id == page_id, WikiPage.kb_id == knowledge.id)
        .with_for_update()
    )
    if (
        page is None
        or page.row_version != snapshot["row_version"]
        or generation_config_hash(knowledge.llm_id, knowledge.parser_config)
        != snapshot["config_hash"]
    ):
        raise WikiDeferred("WIKI_CAS_CONFLICT")
    actual = list(
        session.scalars(
            select(WikiPageSource).where(
                WikiPageSource.kb_id == knowledge.id, WikiPageSource.page_id == page.id
            )
        )
    )
    stamps = {(str(s.document_id), s.document_wiki_version, s.content_hash) for s in actual}
    if stamps != {(s["document_id"], s["version"], s["content_hash"]) for s in snapshot["sources"]}:
        raise WikiDeferred("WIKI_CAS_CONFLICT")
    for source in snapshot["sources"]:
        doc, state = documents[source["document_id"]], states[source["document_id"]]
        if (
            doc.status != 1
            or not state
            or not state.source_ready
            or state.requested_version != source["version"]
            or state.mapped_version != source["version"]
        ):
            raise WikiDeferred("WIKI_INPUT_CHANGED")
    for participant in snapshot["participants"]:
        doc, state = documents[participant["document_id"]], states[participant["document_id"]]
        if (
            not state
            or state.requested_version != participant["requested_version"]
            or state.status == "cancelled"
            or doc.run == 2
        ):
            raise WikiDeferred("WIKI_PARTICIPANT_CHANGED")
        task = session.get(WikiDocumentPageTask, as_uuid(participant["subtask_id"]))
        if not task or task.status in TERMINAL_TASK_STATUSES:
            raise WikiDeferred("WIKI_PARTICIPANT_CHANGED")
    result["content"] = result["content"].replace("\r\n", "\n").replace("\r", "\n").strip()
    result["summary"] = result["summary"].strip()
    updated = session.execute(
        update(WikiPage)
        .where(WikiPage.id == page.id, WikiPage.row_version == snapshot["row_version"])
        .values(row_version=snapshot["row_version"] + 1)
        .execution_options(synchronize_session=False)
    )
    if updated.rowcount != 1:
        raise WikiDeferred("WIKI_CAS_CONFLICT")
    content_hash = fingerprint(
        {
            "title": page.title.strip(),
            "content": result["content"].strip(),
            "summary": result["summary"].strip(),
            "page_type": page.page_type,
            "aliases": sorted({normalize_name(alias) for alias in page.aliases}),
        }
    )
    changed = page.published_content_hash != content_hash
    page.content, page.summary = result["content"], result["summary"]
    page.status = "ready" if actual else "unavailable"
    if actual and (page.version == 0 or changed):
        page.version += 1
    if actual:
        page.published_content_hash = content_hash
    page.row_version = snapshot["row_version"] + 1
    page.failure_attempt, page.defer_count, page.error_code = 0, 0, None
    page.defer_started_at = None
    page.published_sources = [
        {
            "document_id": s["document_id"],
            "version": s["version"],
            "content_hash": s["content_hash"],
        }
        for s in snapshot["sources"]
    ]
    session.execute(delete(WikiPageLink).where(WikiPageLink.source_page_id == page.id))
    for target in sorted(set(result["links"])):
        session.add(WikiPageLink(**scope, source_page_id=page.id, target_page_id=as_uuid(target)))
    for participant in snapshot["participants"]:
        task = session.get(WikiDocumentPageTask, as_uuid(participant["subtask_id"]))
        task.status, task.error_code = "succeeded", None
    session.flush()
    for doc_id in sorted({p["document_id"] for p in snapshot["participants"]}):
        settle_progress(session, documents[doc_id], states[doc_id])
    return {"page_id": str(page.id), "version": page.version, "status": page.status}
