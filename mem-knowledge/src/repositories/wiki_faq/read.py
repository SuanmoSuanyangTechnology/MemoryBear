"""列表全库计数不受筛选影响；审核按每条 FAQ 的真实 Wiki 基线取数。"""

from sqlalchemy import case, func, or_, select

from ...models.owned.wiki import WikiPage
from ...models.owned.wiki_faq import WikiFAQ, WikiFAQGenerationJob, WikiPageVersion
from ...utils.datetime_utils import to_timestamp_ms
from ..wiki.common import as_uuid
from ..wiki.read import begin_read
from .common import (
    get_faq,
    get_version,
    missing,
    prefetch_sources,
    scoped,
    serialize,
    snapshot_data,
)


def list_faqs(
    session, scope, *, review_status=None, is_enabled=None, keyword=None, page=1, limit=20
):
    begin_read(session, scope)
    status = case(
        (WikiFAQ.review_state == "pending", "pending"),
        (
            (WikiFAQ.wiki_page_id.is_not(None))
            & (WikiPage.version != WikiPageVersion.page_version),
            "change_pending",
        ),
        else_="approved",
    )
    base = (
        select(WikiFAQ.id, status.label("review_status"))
        .outerjoin(WikiPage, WikiPage.id == WikiFAQ.wiki_page_id)
        .outerjoin(WikiPageVersion, WikiPageVersion.id == WikiFAQ.approved_wiki_snapshot_id)
        .where(*scoped(WikiFAQ, scope), WikiFAQ.deleted_at.is_(None))
    )
    counts = {"pending": 0, "approved": 0, "change_pending": 0}
    total_set = base.subquery()
    counts.update(
        dict(
            session.execute(
                select(
                    total_set.c.review_status,
                    func.count(),
                ).group_by(total_set.c.review_status)
            ).all()
        )
    )
    if review_status:
        base = base.where(status == review_status)
    if is_enabled is not None:
        base = base.where(WikiFAQ.is_enabled == is_enabled)
    keyword = (keyword or "").strip()
    if keyword:
        base = base.where(
            or_(
                WikiFAQ.standard_question.icontains(keyword, autoescape=True),
                WikiFAQ.answers[0].astext.icontains(keyword, autoescape=True),
            )
        )
    selected = base.subquery()
    total = session.scalar(select(func.count()).select_from(selected))
    rows = list(
        session.scalars(
            select(WikiFAQ)
            .where(WikiFAQ.id.in_(select(selected.c.id)))
            .order_by(WikiFAQ.updated_at.desc(), WikiFAQ.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
    )
    prefetch_sources(session, scope, rows)
    return {
        "items": [serialize(session, scope, row) for row in rows],
        "page": {
            "page": page,
            "pagesize": limit,
            "total": total,
            "hasnext": page * limit < total,
        },
        "total_all": sum(counts.values()),
        "review_counts": {"all": sum(counts.values()), **counts},
    }


def detail(session, scope, faq_id):
    begin_read(session, scope)
    return serialize(session, scope, get_faq(session, scope, faq_id))


def review_context(session, scope, faq_id, *, begin=True):
    if begin:
        begin_read(session, scope)
    row = get_faq(session, scope, faq_id)
    data = serialize(session, scope, row)
    before = get_version(
        session, scope, row.approved_wiki_snapshot_id or row.generated_wiki_snapshot_id
    )
    after = (
        session.scalar(
            select(WikiPageVersion).where(
                *scoped(WikiPageVersion, scope),
                WikiPageVersion.wiki_page_id == row.wiki_page_id,
                WikiPageVersion.page_version == data["source"]["latest_wiki_version"],
            )
        )
        if row.wiki_page_id
        else None
    )
    available = data["source"]["source_status"] == "available" and after is not None
    return {
        "faq": data,
        "source": data["source"],
        "baseline_kind": "approved"
        if row.approved_wiki_snapshot_id
        else ("generated" if row.generated_wiki_snapshot_id else "none"),
        "before": snapshot_data(before),
        "after": snapshot_data(after),
        "source_changed": data["source_changed"],
        "can_approve": available and data["review_status"] != "approved",
        "expected_row_version": row.row_version,
        "reviewed_wiki_snapshot_id": str(after.id) if after else None,
    }


def validate_batch(session, scope, ids, *, begin=True):
    if begin:
        begin_read(session, scope)
    requested = {as_uuid(value) for value in ids}
    found = set(
        session.scalars(
            select(WikiFAQ.id).where(
                *scoped(WikiFAQ, scope),
                WikiFAQ.id.in_(requested),
            )
        )
    )
    if found != requested:
        # 不泄露其他知识库/租户条目的存在性，拒绝整个越界批次。
        missing()


def batch_context(session, scope, ids):
    begin_read(session, scope)
    validate_batch(session, scope, ids, begin=False)
    from ...errors import KnowledgeError

    items = []
    for faq_id in ids:
        try:
            items.append(
                {
                    "faq_id": str(faq_id),
                    "context": review_context(session, scope, faq_id, begin=False),
                }
            )
        except KnowledgeError as exc:
            items.append({"faq_id": str(faq_id), "error_code": exc.code})
    return {"items": items}


def job_data(row):
    return {
        "id": str(row.id),
        "wiki_page_id": str(row.wiki_page_id),
        "source_snapshot_id": str(row.source_snapshot_id),
        "source_version": 1,
        "status": row.status,
        "publish_confirmed": row.publish_confirmed,
        "task_id": str(row.task_id),
        "result_count": row.result_count,
        "failure_attempt": row.failure_attempt,
        "dispatch_attempt": row.dispatch_attempt,
        "row_version": row.row_version,
        "error_code": row.error_code,
        "error_stage": row.error_stage,
        "updated_at_ms": to_timestamp_ms(row.updated_at),
    }


def list_jobs(session, scope, *, wiki_page_id=None, page=1, limit=20):
    begin_read(session, scope)
    query = select(WikiFAQGenerationJob).where(*scoped(WikiFAQGenerationJob, scope))
    if wiki_page_id:
        query = query.where(WikiFAQGenerationJob.wiki_page_id == as_uuid(wiki_page_id))
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(WikiFAQGenerationJob.created_at.desc(), WikiFAQGenerationJob.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return {
        "items": [job_data(row) for row in rows],
        "page": {
            "page": page,
            "pagesize": limit,
            "total": total,
            "hasnext": page * limit < total,
        },
    }
