"""FAQ 写入统一锁知识库、页面、FAQ；编辑可确认已变更来源，启停保持独立。"""

import logging
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import KnowledgeError
from ...models.owned.wiki import WikiPage
from ...models.owned.wiki_faq import WikiFAQ, WikiPageVersion
from ...rag.faq.contracts import FAQContent, edit_content
from ...utils.datetime_utils import utcnow_naive
from ..wiki.common import lock_knowledge
from .common import conflict, get_faq, get_version, review_status, scoped, serialize, source

logger = logging.getLogger(__name__)


def _lock(session, scope, faq_id):
    lock_knowledge(session, scope["kb_id"], scope)
    row = get_faq(session, scope, faq_id)
    if row.wiki_page_id:
        session.scalar(
            select(WikiPage)
            .where(
                *scoped(WikiPage, scope),
                WikiPage.id == row.wiki_page_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return get_faq(session, scope, faq_id, lock=True)


def _expected(row, version):
    if row.row_version != version:
        conflict()


def _touch(row, actor_id):
    row.row_version += 1
    row.updated_by = actor_id
    row.updated_at = utcnow_naive()


def create(session, scope, actor_id, payload):
    _, actual_scope = lock_knowledge(session, scope["kb_id"], scope)
    row = WikiFAQ(
        id=uuid.uuid4(),
        **actual_scope,
        origin="manual",
        standard_question=payload["standard_question"],
        answers=[payload["standard_answer"]],
        similar_questions=[],
        negative_questions=[],
        answer_strategy="all",
        review_state="approved",
        is_enabled=False,
        content_revision=1,
        row_version=1,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(row)
    session.flush()
    return serialize(session, scope, row)


def edit(session, scope, faq_id, actor_id, payload):
    row = _lock(session, scope, faq_id)
    _expected(row, payload["expected_row_version"])
    try:
        current = FAQContent(
            standard_question=row.standard_question,
            similar_questions=row.similar_questions,
            negative_questions=row.negative_questions,
            answers=row.answers,
            answer_strategy=row.answer_strategy,
        )
        merged = edit_content(current, payload["standard_question"], payload["standard_answer"])
    except ValidationError as exc:
        logger.warning(
            "FAQ 编辑字段冲突 faq_id=%s stage=validate_content error_type=%s errors=%s",
            faq_id,
            type(exc).__name__,
            exc.errors(include_input=False, include_url=False, include_context=False),
        )
        raise KnowledgeError.from_code("KB_FAQ_CONTENT_INVALID") from exc
    target = None
    if row.review_state == "approved" and row.wiki_page_id is not None:
        info = source(session, scope, row)
        if review_status(row, info) == "change_pending":
            # 保存即确认当前来源基线；来源不可用也允许确认，不影响待审核条目。
            target = _latest_snapshot(session, scope, row, info["latest_wiki_version"])
    content_changed = merged != current
    if content_changed:
        row.standard_question, row.answers = merged.standard_question, merged.answers
        row.content_revision += 1
    if target is not None:
        return _record_approval(session, scope, row, actor_id, target)["faq"]
    if content_changed:
        _touch(row, actor_id)
        session.flush()
    return serialize(session, scope, row)


def set_enabled(session, scope, faq_id, actor_id, payload):
    row = _lock(session, scope, faq_id)
    _expected(row, payload["expected_row_version"])
    if payload["is_enabled"] and row.review_state == "pending":
        conflict("KB_FAQ_REVIEW_REQUIRED")
    changed = row.is_enabled != payload["is_enabled"]
    if changed:
        row.is_enabled = payload["is_enabled"]
        _touch(row, actor_id)
        session.flush()
    return {"changed": changed, "faq": serialize(session, scope, row)}


def delete(session, scope, faq_id, actor_id, payload):
    row = _lock(session, scope, faq_id)
    _expected(row, payload["expected_row_version"])
    row.deleted_at = utcnow_naive()
    row.is_enabled = False
    _touch(row, actor_id)
    session.flush()
    return {"id": str(row.id), "deleted": True, "row_version": row.row_version}


def approve(session, scope, faq_id, actor_id, payload):
    row = _lock(session, scope, faq_id)
    target_id = payload["reviewed_wiki_snapshot_id"]
    # 相同内容与Wiki快照已通过时直接返回；不能推进后来Wiki版本的审核基线。
    if (
        target_id is not None
        and row.review_state == "approved"
        and row.approved_wiki_snapshot_id == target_id
        and row.content_revision == payload["content_revision"]
    ):
        return {"changed": False, "faq": serialize(session, scope, row)}
    _expected(row, payload["expected_row_version"])
    if row.content_revision != payload["content_revision"]:
        conflict()
    if row.review_state == "approved" and row.wiki_page_id is None and target_id is None:
        # 只跳过已通过的无来源条目，不能将缺失快照解释成“审核当前最新版”。
        return {"changed": False, "faq": serialize(session, scope, row)}
    info = source(session, scope, row)
    if info["source_status"] != "available":
        conflict("KB_FAQ_SOURCE_UNAVAILABLE")
    target = get_version(session, scope, target_id)
    if (
        not target
        or target.wiki_page_id != row.wiki_page_id
        or target.page_version != info["latest_wiki_version"]
    ):
        conflict("KB_FAQ_SOURCE_CHANGED")
    return _record_approval(session, scope, row, actor_id, target)


def approve_latest(session: Session, scope: dict, faq_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
    """批量审核在锁内接受当前问答，推进至来源最新快照；重复调用不重复写入。"""
    row = _lock(session, scope, faq_id)
    if row.review_state == "approved" and row.wiki_page_id is None:
        return {"changed": False, "faq": serialize(session, scope, row)}
    info = source(session, scope, row)
    if info["source_status"] != "available":
        conflict("KB_FAQ_SOURCE_UNAVAILABLE")
    target = _latest_snapshot(session, scope, row, info["latest_wiki_version"])
    if row.review_state == "approved" and row.approved_wiki_snapshot_id == target.id:
        return {"changed": False, "faq": serialize(session, scope, row)}
    return _record_approval(session, scope, row, actor_id, target)


def _latest_snapshot(
    session: Session, scope: dict, row: WikiFAQ, wiki_version: int
) -> WikiPageVersion:
    target = session.scalar(
        select(WikiPageVersion).where(
            *scoped(WikiPageVersion, scope),
            WikiPageVersion.wiki_page_id == row.wiki_page_id,
            WikiPageVersion.page_version == wiki_version,
        )
    )
    if target is None:
        logger.warning(
            "FAQ 当前 Wiki 快照缺失 faq_id=%s wiki_page_id=%s wiki_version=%s "
            "stage=load_latest_snapshot error_type=KnowledgeError error_code=KB_FAQ_SOURCE_CHANGED",
            row.id,
            row.wiki_page_id,
            wiki_version,
        )
        conflict("KB_FAQ_SOURCE_CHANGED")
    return target


def _record_approval(
    session: Session, scope: dict, row: WikiFAQ, actor_id: uuid.UUID, target: WikiPageVersion
) -> dict:
    row.review_state = "approved"
    row.approved_wiki_snapshot_id = target.id
    row.reviewed_by, row.reviewed_at = actor_id, utcnow_naive()
    _touch(row, actor_id)
    session.flush()
    logger.info(
        "FAQ 审核通过 faq_id=%s wiki_page_id=%s wiki_version=%s content_revision=%s actor_id=%s",
        row.id,
        row.wiki_page_id,
        target.page_version,
        row.content_revision,
        actor_id,
    )
    return {"changed": True, "faq": serialize(session, scope, row)}
