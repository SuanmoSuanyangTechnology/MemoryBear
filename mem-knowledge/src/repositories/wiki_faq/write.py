"""FAQ 写入统一锁知识库、页面、FAQ；编辑与审核/启停互不联动。"""

import logging
import uuid

from pydantic import ValidationError
from sqlalchemy import select

from ...errors import KnowledgeError
from ...models.owned.wiki import WikiPage
from ...models.owned.wiki_faq import WikiFAQ
from ...rag.faq.contracts import FAQContent, edit_content
from ...utils.datetime_utils import utcnow_naive
from ..wiki.common import lock_knowledge
from .common import conflict, get_faq, get_version, scoped, serialize, source

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
    if merged != current:
        row.standard_question, row.answers = merged.standard_question, merged.answers
        row.content_revision += 1
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
    target_id, request_id = payload["reviewed_wiki_snapshot_id"], payload["idempotency_key"]
    # 重放成功请求只可读取原结果，不能在后续编辑后再次推进审核。
    if row.last_review_request_id == request_id:
        if (
            row.approved_wiki_snapshot_id != target_id
            or row.content_revision != payload["content_revision"]
        ):
            conflict()
        return {"changed": False, "faq": serialize(session, scope, row)}
    _expected(row, payload["expected_row_version"])
    if row.content_revision != payload["content_revision"]:
        conflict()
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
    if row.review_state == "approved" and row.approved_wiki_snapshot_id == target_id:
        return {"changed": False, "faq": serialize(session, scope, row)}
    row.review_state = "approved"
    row.approved_wiki_snapshot_id = target_id
    row.last_review_request_id = request_id
    row.reviewed_by, row.reviewed_at = actor_id, utcnow_naive()
    _touch(row, actor_id)
    session.flush()
    logger.info(
        "FAQ 审核通过 faq_id=%s wiki_page_id=%s wiki_version=%s actor_id=%s",
        row.id,
        row.wiki_page_id,
        target.page_version,
        actor_id,
    )
    return {"changed": True, "faq": serialize(session, scope, row)}
