"""One finite keyset traversal per Wiki enable event; no background scan service."""

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from ...models.owned import Document, Knowledge
from ...rag.wiki.config import read_wiki_config
from ...repositories.wiki.common import as_uuid
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
    cursor = None
    accepted = 0
    while batch := transaction(
        runtime, _batch, kb_id, upper_at_ms, cursor, runtime.settings.kb_wiki_backfill_batch_size
    ):
        for document_id, created_at in batch:
            delivery = transaction(runtime, admit, kb_id, document_id, admission_id=admission_id)
            if delivery:
                publish_map(runtime, delivery)
                accepted += bool(delivery["accepted"])
            cursor = (created_at, document_id)
    return {"accepted_documents": accepted}
