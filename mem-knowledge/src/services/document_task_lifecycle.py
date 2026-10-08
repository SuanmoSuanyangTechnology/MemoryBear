"""Shared deletion checks for document parsing and QA import workers."""

from __future__ import annotations

import logging
import uuid

from ..models.owned import Document
from ..rag.vdb.vector_store import TaskVectorStore, collection_name_for_knowledge
from ..runtime import ProcessRuntime
from ..tasks.state import PARSE_CANCEL_KEY
from .document_mutation_guard import sync_document_mutation_guard

logger = logging.getLogger(__name__)


class DocumentTaskAborted(RuntimeError):
    """The document was deleted or its processing was cancelled."""


def ensure_document_active(runtime: ProcessRuntime, document_id: uuid.UUID) -> None:
    """Use the database as a durable fence when a short Redis marker expires."""

    with runtime.database.sync_session() as session:
        if session.get(Document, document_id) is None:
            raise DocumentTaskAborted(f"Document no longer exists: {document_id}")
    try:
        cancelled = runtime.redis.sync_client().get(PARSE_CANCEL_KEY.format(doc_id=document_id))
    except Exception as exc:
        # Preserve the parser's database fallback during Redis read outages.
        # Actual ES writes still require acquiring and validating their lease.
        logger.warning(
            "Document cancellation state unavailable: document=%s error_type=%s",
            document_id,
            type(exc).__name__,
        )
        return
    if cancelled is not None:
        raise DocumentTaskAborted(f"Document processing cancelled: {document_id}")


def cleanup_late_write_if_deleted(
    runtime: ProcessRuntime,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
    """Best-effort compensation after an in-flight ES request loses its lease."""

    with sync_document_mutation_guard(
        runtime.redis.sync_client(), document_id
    ) as guard:
        with runtime.database.sync_session() as session:
            if session.get(Document, document_id) is not None:
                return
        guard.ensure_owned()
        client = runtime.elasticsearch.sync_client()
        index = collection_name_for_knowledge(knowledge_id)
        if not client.indices.exists(index=index):
            return
        refresh_result = client.indices.refresh(index=index)
        if refresh_result.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Elasticsearch refresh failed during late-write cleanup")
        store = TaskVectorStore(client, knowledge_id, None)
        store.delete_by_metadata_field("document_id", str(document_id), refresh=True)
        guard.ensure_owned()
        logger.warning(
            "Removed late document vectors after lease loss: knowledge=%s document=%s",
            knowledge_id,
            document_id,
        )


__all__ = [
    "DocumentTaskAborted",
    "cleanup_late_write_if_deleted",
    "ensure_document_active",
]
