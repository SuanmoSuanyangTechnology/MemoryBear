"""FAQ 召回快照与资格；只返回值，不跨线程传递 ORM 或 Session。"""

from sqlalchemy import exists, or_, select

from ...models.owned import Knowledge, WikiPage
from ...models.owned.wiki_faq import WikiFAQ
from ...models.references import Workspace
from ...rag.faq.contracts import readable_faq_text
from ..wiki.common import as_uuid
from .common import get_faq, scoped


def eligible(scope):
    # 已审核 FAQ 使用自己的问答；来源不可用只触发复核，不暂停召回。
    published_page = exists(
        select(WikiPage.id).where(
            WikiPage.id == WikiFAQ.wiki_page_id,
            *scoped(WikiPage, scope),
            WikiPage.version > 0,
        )
    )
    return (
        *scoped(WikiFAQ, scope),
        WikiFAQ.review_state == "approved",
        WikiFAQ.is_enabled.is_(True),
        WikiFAQ.deleted_at.is_(None),
        or_(WikiFAQ.wiki_page_id.is_(None), published_page),
    )


def data(row):
    return {
        "id": str(row.id),
        "content_revision": row.content_revision,
        "row_version": row.row_version,
        "standard_question": readable_faq_text(row.standard_question),
        "similar_questions": list(row.similar_questions),
        "negative_questions": list(row.negative_questions),
        "answers": [readable_faq_text(answer) for answer in row.answers],
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


def was_deleted(session, scope, faq_id):
    # 仅为已通过鉴权的写入做收尾；精确校验原作用域中的软删除墓碑，不扩大读取权限。
    return session.scalar(select(WikiFAQ.id).where(
        *scoped(WikiFAQ, scope), WikiFAQ.id == as_uuid(faq_id), WikiFAQ.deleted_at.is_not(None)
    )) is not None


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
