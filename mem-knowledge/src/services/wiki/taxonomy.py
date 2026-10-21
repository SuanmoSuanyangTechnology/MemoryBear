"""显式目录重规划：先预览，核对快照后仅更新目录归属。"""

from sqlalchemy import select

from ...models.owned import WikiPage
from ...rag.wiki.config import WikiError, fingerprint, read_wiki_config
from ...rag.wiki.contracts import FolderAssignment
from ...rag.wiki.planning import taxonomy, validate_category_path
from ...repositories.wiki.common import as_uuid, lock_knowledge
from ...repositories.wiki.pages import _folder, _planning_context
from ...repositories.wiki.read import scoped_pages
from .lease import taxonomy_lease
from .runtime import make_model, transaction

PLAN_FORMAT = "wiki_taxonomy_replan_v1"


def _target(runtime, kb_id):
    return {"db_host": runtime.settings.db_host, "db_name": runtime.settings.db_name,
            "kb_id": str(as_uuid(kb_id))}


def _page_fingerprint(page):
    return fingerprint([
        str(page.id), page.row_version, str(page.folder_id) if page.folder_id else None,
        page.title, page.page_type, page.topic_scope, page.aliases,
        page.summary, page.content, page.published_sources,
    ])


def _snapshot(session, kb_id, expected_scope=None):
    knowledge, scope = lock_knowledge(session, kb_id, expected_scope)
    context = _planning_context(session, knowledge.id)
    indexed = {item["page_id"]: item for item in context["pages"]}
    entries = []
    for page in session.scalars(select(WikiPage).where(
        scoped_pages(scope), WikiPage.page_type != "summary"
    ).order_by(WikiPage.id)):
        item = indexed[str(page.id)]
        entries.append({
            "page_id": str(page.id), "name": page.title, "kind": page.page_type,
            "scope": page.topic_scope, "aliases": page.aliases, "description": page.summary,
            "previous_path": item["folder_path"], "folder_id": item["folder_id"],
            "fingerprint": _page_fingerprint(page),
        })
    return {"scope": {key: str(value) for key, value in scope.items()},
            "llm_id": str(knowledge.llm_id) if knowledge.llm_id else None,
            "language": read_wiki_config(knowledge.parser_config).language,
            "revision": context["revision"], "entries": entries}


def preview_taxonomy(runtime, kb_id):
    snapshot = transaction(runtime, _snapshot, kb_id)
    if snapshot["entries"] and not snapshot["llm_id"]:
        raise WikiError("WIKI_MODEL_REQUIRED", retryable=False, diagnostics={
            "stage": "taxonomy_preview", "knowledge_id": str(kb_id),
            "error_message": "知识库尚未配置用于目录规划的模型",
        })
    topics = [{**item, "topic_ref": f"T{i + 1}"} for i, item in enumerate(snapshot["entries"])]
    if topics:
        model = make_model(runtime, {**snapshot["scope"], "llm_id": snapshot["llm_id"]})
        # 显式重规划不把待纠正的旧目录当作既定分类，所有可读主题一起作为输入。
        taxonomy(model, {"language": snapshot["language"]}, topics, {"pages": [], "folders": []})
    return {
        "format": PLAN_FORMAT, "target": _target(runtime, kb_id), "scope": snapshot["scope"],
        "revision": snapshot["revision"],
        "pages": [{key: topic[key] for key in (
            "page_id", "name", "kind", "previous_path", "category_path", "fingerprint"
        )} for topic in topics],
    }


def _apply(session, kb_id, plan, ensure_owned):
    snapshot = _snapshot(session, kb_id, plan["scope"])
    current = {item["page_id"]: item for item in snapshot["entries"]}
    proposed = {item["page_id"]: item for item in plan["pages"]}
    if (len(proposed) != len(plan["pages"]) or set(current) != set(proposed)
            or snapshot["revision"] != plan["revision"]
            or any(current[key]["fingerprint"] != proposed[key]["fingerprint"] for key in current)):
        raise WikiError("WIKI_TAXONOMY_CHANGED", retryable=False, diagnostics={
            "stage": "taxonomy_apply", "knowledge_id": str(kb_id),
            "expected_revision": plan["revision"], "current_revision": snapshot["revision"],
            "error_message": "预览后的页面集合、目录或内容已变化，或计划包含重复页面，请重新预览",
        })
    # 全量校验后才开始写入，任何无效路径都使事务整体回滚。
    for item in proposed.values():
        assignment = FolderAssignment.model_validate({
            "topic_ref": item["page_id"], "category_path": item["category_path"],
        })
        validate_category_path(assignment.category_path)
    _, scope = lock_knowledge(session, kb_id, plan["scope"])
    locked = list(session.scalars(select(WikiPage).where(
        WikiPage.kb_id == as_uuid(kb_id), WikiPage.id.in_([as_uuid(key) for key in current])
    ).order_by(WikiPage.id).with_for_update().execution_options(populate_existing=True)))
    if len(locked) != len(current) or any(
        _page_fingerprint(page) != proposed[str(page.id)]["fingerprint"] for page in locked
    ):
        raise WikiError("WIKI_TAXONOMY_CHANGED", retryable=False, diagnostics={
            "stage": "taxonomy_apply_lock", "knowledge_id": str(kb_id),
            "error_message": "取得页面写锁时发现页面已被修改或删除，已拒绝应用过期目录计划",
        })
    changed = []
    for page in locked:
        path = proposed[str(page.id)]["category_path"]
        if path == current[str(page.id)]["previous_path"]:
            continue
        page.folder_id = _folder(session, scope, path)
        page.row_version += 1
        changed.append(str(page.id))
    ensure_owned()
    return {"kb_id": str(kb_id), "changed_page_ids": changed, "changed_pages": len(changed)}


def apply_taxonomy(runtime, kb_id, plan):
    if plan.get("format") != PLAN_FORMAT or plan.get("target") != _target(runtime, kb_id):
        raise WikiError("WIKI_TAXONOMY_TARGET_MISMATCH", retryable=False, diagnostics={
            "stage": "taxonomy_apply", "knowledge_id": str(kb_id),
            "target": _target(runtime, kb_id),
            "error_message": "计划格式、数据库或知识库目标不匹配，已拒绝应用",
        })
    with taxonomy_lease(runtime.redis.sync_client(), kb_id) as ensure_owned:
        return transaction(runtime, _apply, kb_id, plan, ensure_owned)
