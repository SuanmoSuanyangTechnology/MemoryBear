"""FAQ 作用域、状态投影及权限一致的来源读取。"""

from sqlalchemy import select

from ...errors import KnowledgeError
from ...models.owned.wiki import WikiPage
from ...models.owned.wiki_faq import WikiFAQ, WikiPageVersion
from ...utils.datetime_utils import to_timestamp_ms
from ..wiki.common import as_uuid
from ..wiki.read import scoped_pages


def scoped(model, scope):
    return [
        getattr(model, key) == as_uuid(scope[key]) for key in ("tenant_id", "workspace_id", "kb_id")
    ]


def missing():
    raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND")


def conflict(code="KB_FAQ_CONFLICT"):
    raise KnowledgeError.from_code(code)


def get_faq(session, scope, faq_id, *, lock=False):
    query = select(WikiFAQ).where(
        *scoped(WikiFAQ, scope),
        WikiFAQ.id == as_uuid(faq_id),
        WikiFAQ.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(query)
    if row is None:
        missing()
    return row


def get_version(session, scope, version_id):
    if version_id is None:
        return None
    cache = session.info.get("faq_version_cache", {})
    if version_id in cache:
        return cache[version_id]
    row = session.scalar(
        select(WikiPageVersion).where(
            *scoped(WikiPageVersion, scope),
            WikiPageVersion.id == version_id,
        )
    )
    if row is None:
        missing()
    return row


def prefetch_sources(session, scope, rows):
    """列表按集合预取来源，避免逐条查询重复页面及快照。"""
    page_ids = {row.wiki_page_id for row in rows if row.wiki_page_id}
    version_ids = {
        value
        for row in rows
        for value in (row.approved_wiki_snapshot_id, row.generated_wiki_snapshot_id)
        if value
    }
    pages = (
        {
            p.id: p
            for p in session.scalars(
                select(WikiPage).where(
                    *scoped(WikiPage, scope),
                    WikiPage.id.in_(page_ids),
                )
            )
        }
        if page_ids
        else {}
    )
    latest = (
        list(
            session.scalars(
                select(WikiPageVersion)
                .join(
                    WikiPage,
                    (WikiPage.id == WikiPageVersion.wiki_page_id)
                    & (WikiPage.version == WikiPageVersion.page_version),
                )
                .where(*scoped(WikiPageVersion, scope), WikiPage.id.in_(page_ids))
            )
        )
        if page_ids
        else []
    )
    versions = (
        list(
            session.scalars(
                select(WikiPageVersion).where(
                    *scoped(WikiPageVersion, scope),
                    WikiPageVersion.id.in_(version_ids),
                )
            )
        )
        if version_ids
        else []
    )
    available = (
        set(
            session.scalars(
                select(WikiPage.id).where(
                    WikiPage.id.in_(page_ids),
                    scoped_pages(scope),
                )
            )
        )
        if page_ids
        else set()
    )
    session.info["faq_source_cache"] = (pages, {v.wiki_page_id: v for v in latest}, available)
    session.info["faq_version_cache"] = {v.id: v for v in [*versions, *latest]}


def source(session, scope, row):
    if row.wiki_page_id is None:
        return {
            "wiki_page_id": None,
            "title": None,
            "source_status": "none",
            "generated_wiki_version": None,
            "approved_wiki_version": None,
            "latest_wiki_version": None,
            "latest_published_at_ms": None,
        }
    cache = session.info.get("faq_source_cache")
    page = (
        cache[0].get(row.wiki_page_id)
        if cache
        else session.scalar(
            select(WikiPage).where(
                *scoped(WikiPage, scope),
                WikiPage.id == row.wiki_page_id,
            )
        )
    )
    before = get_version(session, scope, row.approved_wiki_snapshot_id)
    generated = get_version(session, scope, row.generated_wiki_snapshot_id)
    latest = (
        cache[1].get(row.wiki_page_id)
        if cache
        else (
            session.scalar(
                select(WikiPageVersion).where(
                    *scoped(WikiPageVersion, scope),
                    WikiPageVersion.wiki_page_id == row.wiki_page_id,
                    WikiPageVersion.page_version == page.version,
                )
            )
            if page
            else None
        )
    )
    available = (
        row.wiki_page_id in cache[2]
        if cache
        else bool(
            page
            and session.scalar(
                select(WikiPage.id).where(
                    WikiPage.id == page.id,
                    scoped_pages(scope),
                )
            )
        )
    )
    return {
        "wiki_page_id": str(row.wiki_page_id),
        "title": latest.title if latest else (page.title if page else None),
        "source_status": "available" if available else "unavailable",
        "generated_wiki_version": generated.page_version if generated else None,
        "approved_wiki_version": before.page_version if before else None,
        "latest_wiki_version": page.version if page else None,
        "latest_published_at_ms": to_timestamp_ms(latest.published_at) if latest else None,
    }


def review_status(row, source_info):
    if row.review_state == "pending":
        return "pending"
    if (
        row.wiki_page_id is not None
        and source_info["latest_wiki_version"] is not None
        and source_info["approved_wiki_version"] != source_info["latest_wiki_version"]
    ):
        return "change_pending"
    return "approved"


def serialize(session, scope, row):
    info = source(session, scope, row)
    baseline = info["approved_wiki_version"] or info["generated_wiki_version"]
    return {
        "id": str(row.id),
        "standard_question": row.standard_question,
        "standard_answer": row.answers[0],
        "answer_preview": row.answers[0][:200],
        "review_status": review_status(row, info),
        "is_enabled": row.is_enabled,
        "can_enable": row.review_state != "pending",
        "origin": row.origin,
        "source": info,
        "source_changed": bool(baseline and info["latest_wiki_version"] != baseline),
        "content_revision": row.content_revision,
        "row_version": row.row_version,
        "created_at_ms": to_timestamp_ms(row.created_at),
        "updated_at_ms": to_timestamp_ms(row.updated_at),
        "reviewed_at_ms": to_timestamp_ms(row.reviewed_at),
    }


def snapshot_data(row):
    if row is None:
        return None
    return {
        "snapshot_id": str(row.id),
        "version": row.page_version,
        "title": row.title,
        "content": row.content,
        "summary": row.summary,
        "published_at_ms": to_timestamp_ms(row.published_at),
    }
