"""FAQ 召回快照与资格；只返回值，不跨线程传递 ORM 或 Session。"""

from sqlalchemy import exists, or_, select

from ...models.owned import Knowledge, WikiPage
from ...models.owned.wiki_faq import WikiFAQ
from ...models.references import Workspace
from ..wiki.read import scoped_pages
from .common import get_faq, scoped


def eligible(scope):
    available_page = exists(
        select(WikiPage.id).where(WikiPage.id == WikiFAQ.wiki_page_id, scoped_pages(scope))
    )
    return (
        *scoped(WikiFAQ, scope),
        WikiFAQ.review_state == "approved",
        WikiFAQ.is_enabled.is_(True),
        WikiFAQ.deleted_at.is_(None),
        or_(WikiFAQ.wiki_page_id.is_(None), available_page),
    )


def data(row):
    return {
        "id": str(row.id),
        "content_revision": row.content_revision,
        "row_version": row.row_version,
        "standard_question": row.standard_question,
        "similar_questions": list(row.similar_questions),
        "negative_questions": list(row.negative_questions),
        "answers": list(row.answers),
        "answer_strategy": row.answer_strategy,
        "wiki_page_id": str(row.wiki_page_id) if row.wiki_page_id else None,
    }


def load_for_sync(session, scope, faq_id, expected_row_version=None):
    from ..wiki.read import begin_read
    from .common import conflict

    begin_read(session, scope)
    row = get_faq(session, scope, faq_id)
    if expected_row_version is not None and row.row_version != expected_row_version:
        conflict()
    allowed = session.scalar(select(WikiFAQ.id).where(WikiFAQ.id == row.id, *eligible(scope)))
    knowledge = session.get(Knowledge, scope["kb_id"])
    return {
        **data(row),
        "eligible": bool(allowed and knowledge.is_active),
        "embedding_id": knowledge.embedding_id,
    }


async def eligible_versions(db, scope):
    return list(
        (
            await db.execute(
                select(WikiFAQ.id, WikiFAQ.content_revision, WikiFAQ.negative_questions)
                .where(*eligible(scope))
                .order_by(WikiFAQ.id)
            )
        ).all()
    )


async def has_eligible(db, scope):
    return await db.scalar(select(WikiFAQ.id).where(*eligible(scope)).limit(1)) is not None


async def hydrate(db, scope, identities):
    if not identities:
        return {}
    rows = (
        await db.scalars(select(WikiFAQ).where(WikiFAQ.id.in_(identities), *eligible(scope)))
    ).all()
    return {str(row.id): data(row) for row in rows}


async def scope_for_target(db, knowledge):
    tenant_id = await db.scalar(
        select(Workspace.tenant_id).where(Workspace.id == knowledge.workspace_id)
    )
    return {"kb_id": knowledge.id, "workspace_id": knowledge.workspace_id, "tenant_id": tenant_id}
