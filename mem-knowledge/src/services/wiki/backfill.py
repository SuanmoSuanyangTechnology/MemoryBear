"""One finite keyset traversal per Wiki enable event; no background scan service."""

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from ...models.owned import Document, Knowledge, WikiDocumentState
from ...rag.wiki.config import read_wiki_config
from ...repositories.wiki.common import as_uuid, state_delivery
from ...repositories.wiki.state import admit
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


def _unconfirmed(session, kb_id, admission_id, limit):
    return [
        {**state_delivery(state), "publish": True}
        for state in session.scalars(
            select(WikiDocumentState)
            .where(
                WikiDocumentState.kb_id == as_uuid(kb_id),
                WikiDocumentState.admission_id == admission_id,
                WikiDocumentState.publish_confirmed.is_(False),
                WikiDocumentState.stage == "map_pending",
                WikiDocumentState.status == "pending",
            )
            .order_by(WikiDocumentState.document_id)
            .limit(limit)
        )
    ]
