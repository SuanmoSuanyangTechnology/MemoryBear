"""One finite keyset traversal per Wiki enable event; no background scan service."""

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from ...models.owned import Document, Knowledge, WikiDocumentState
from ...rag.wiki.config import read_wiki_config
from ...repositories.wiki.common import as_uuid, lock_knowledge, state_delivery
from ...repositories.wiki.state import admit, record_publish_failure
from .execution import publish_map
from .runtime import transaction


def _batch(session, kb_id, upper_at_ms, cursor, limit):
    knowledge = session.get(Knowledge, as_uuid(kb_id))
    if knowledge is None or not read_wiki_config(knowledge.parser_config).enabled:
        return []
    upper = datetime.fromtimestamp(upper_at_ms / 1000, UTC).replace(tzinfo=None)
    query = select(Document.id, Document.created_at).where(
        Document.kb_id == knowledge.id,
        Document.status == 1,
        Document.created_at <= upper,
    )
    if cursor:
        created, doc_id = cursor
        query = query.where(
            or_(
                Document.created_at > created,
                and_(Document.created_at == created, Document.id > doc_id),
            )
        )
    return [
        (row.id, row.created_at)
        for row in session.execute(query.order_by(Document.created_at, Document.id).limit(limit))
    ]


def register_backfill(session, kb_id, upper_at_ms, admission_id, batch_size):
    """在调用方的配置事务内登记，提交前不发送消息。"""
    cursor = None
    registered = 0
    while batch := _batch(session, kb_id, upper_at_ms, cursor, batch_size):
        for document_id, created_at in batch:
            delivery = admit(
                session, kb_id, document_id,
                admission_id=admission_id, admission_at_ms=upper_at_ms,
            )
            if delivery and delivery.get("publish", True):
                # 恢复明确发布失败的旧 Map 时，保留身份并把交接交给本次补录。
                # 已确认或正在执行的任务不会进入该分支。
                state = session.get(WikiDocumentState, document_id)
                state.admission_id = admission_id
                session.flush()
                registered += 1
            cursor = (created_at, document_id)
    return registered


def record_backfill_publish_failure(session, kb_id, admission_id, batch_size):
    """只标记本事件尚未发出的 Map，迟到错误不能覆盖已执行或新处理轮。"""
    lock_knowledge(session, kb_id)
    cursor = None
    while pending := _unconfirmed(session, kb_id, admission_id, batch_size, cursor):
        for delivery in pending:
            record_publish_failure(session, delivery, terminal=True)
            cursor = as_uuid(delivery["document_id"])
        session.flush()


def execute_backfill(runtime, kb_id, upper_at_ms, admission_id):
    # Resume already accepted children even when admission has since been disabled.
    while pending := transaction(
        runtime, _unconfirmed, kb_id, admission_id, runtime.settings.kb_wiki_backfill_batch_size
    ):
        for delivery in pending:
            publish_map(runtime, delivery)
    cursor = None
    accepted = 0
    while batch := transaction(
        runtime, _batch, kb_id, upper_at_ms, cursor, runtime.settings.kb_wiki_backfill_batch_size
    ):
        for document_id, created_at in batch:
            delivery = transaction(
                runtime, admit, kb_id, document_id,
                admission_id=admission_id, admission_at_ms=upper_at_ms,
            )
            if delivery:
                publish_map(runtime, delivery)
                accepted += bool(delivery["accepted"])
            cursor = (created_at, document_id)
    return {"accepted_documents": accepted}


def _unconfirmed(session, kb_id, admission_id, limit, after_document_id=None):
    query = select(WikiDocumentState).where(
        WikiDocumentState.kb_id == as_uuid(kb_id),
        WikiDocumentState.admission_id == admission_id,
        WikiDocumentState.publish_confirmed.is_(False),
        WikiDocumentState.stage == "map_pending",
        WikiDocumentState.status == "pending",
    )
    if after_document_id is not None:
        query = query.where(WikiDocumentState.document_id > after_document_id)
    return [
        {**state_delivery(state), "publish": True}
        for state in session.scalars(
            query.order_by(WikiDocumentState.document_id).limit(limit)
        )
    ]
