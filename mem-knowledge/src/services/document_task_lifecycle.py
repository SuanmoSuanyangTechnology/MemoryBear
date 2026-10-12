"""Shared deletion checks for document parsing and QA import workers."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from ..errors import KnowledgeError
from ..models.owned import Document
from ..rag.vdb.field import Field
from ..rag.vdb.vector_store import collection_name_for_knowledge
from ..runtime import ProcessRuntime
from ..tasks.state import PARSE_CANCEL_KEY
from .document_mutation_guard import (
    DocumentMutationLeaseLost,
    async_document_mutation_guard,
    sync_document_mutation_guard,
)

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


@asynccontextmanager
async def guard_chunk_mutation(
    runtime: ProcessRuntime,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
    search_client: Any,
) -> AsyncIterator[None]:
    """Revalidate an authorized chunk request after its embedding has finished."""

    redis = await runtime.redis.client()
    mutation_completed = False
    try:
        async with async_document_mutation_guard(redis, document_id) as guard:
            async with runtime.database.async_session() as session:
                document = await session.get(Document, document_id)
                if document is None or document.kb_id != knowledge_id:
                    raise KnowledgeError.from_code("KB_DOCUMENT_NOT_FOUND")
            if await redis.get(PARSE_CANCEL_KEY.format(doc_id=document_id)) is not None:
                raise KnowledgeError.from_code("KB_CONFLICT")
            await guard.ensure_owned()
            yield
            mutation_completed = True
            await guard.ensure_owned()
    except DocumentMutationLeaseLost as exc:
        try:
            document_survives = await _reconcile_chunk_write(
                runtime, redis, search_client, knowledge_id, document_id
            )
            if mutation_completed and document_survives:
                # ES already confirmed success. Let the route perform its
                # matching counter update instead of reporting a false failure.
                logger.warning(
                    "Chunk mutation completed after lease recovery: document=%s", document_id
                )
                return
        except Exception as cleanup_exc:
            logger.error(
                "Late chunk vector cleanup failed: knowledge=%s document=%s error_type=%s",
                knowledge_id,
                document_id,
                type(cleanup_exc).__name__,
            )
        raise KnowledgeError.from_code("KB_SEARCH_UNAVAILABLE") from exc
    except TimeoutError as exc:
        raise KnowledgeError.from_code("KB_SEARCH_UNAVAILABLE") from exc


async def _reconcile_chunk_write(
    runtime: ProcessRuntime,
    redis: Any,
    search_client: Any,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
) -> bool:
    # Import lazily: chunk and document services share this lifecycle boundary.
    from .document import delete_document_search_data

    async with async_document_mutation_guard(redis, document_id) as guard:
        async with runtime.database.async_session() as session:
            if await session.get(Document, document_id) is not None:
                return True
        await guard.ensure_owned()
        await delete_document_search_data(search_client, knowledge_id, document_id)
        await guard.ensure_owned()
        return False


def cleanup_interrupted_task_vectors(
    runtime: ProcessRuntime,
    knowledge_id: uuid.UUID,
    document_id: uuid.UUID,
    chunk_ids: Sequence[str] = (),
) -> None:
    """Clean a deleted document or only this failed attempt's newly created chunks."""

    with sync_document_mutation_guard(runtime.redis.sync_client(), document_id) as guard:
        with runtime.database.sync_session() as session:
            document_exists = session.get(Document, document_id) is not None
        if document_exists and not chunk_ids:
            return
        guard.ensure_owned()
        client = runtime.elasticsearch.sync_client()
        index = collection_name_for_knowledge(knowledge_id)
        if not client.indices.exists(index=index):
            return
        refresh_result = client.indices.refresh(index=index)
        if refresh_result.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Elasticsearch refresh failed during late-write cleanup")
        query = {"term": {Field.DOCUMENT_ID.value: str(document_id)}}
        if document_exists:
            query = {
                "bool": {
                    "filter": [
                        query,
                        {"terms": {Field.DOC_ID.value: list(chunk_ids)}},
                    ]
                }
            }
        guard.ensure_owned()
        result = client.delete_by_query(
            index=index,
            query=query,
            refresh=True,
            conflicts="abort",
            wait_for_completion=True,
        )
        if (
            result.get("timed_out")
            or result.get("failures")
            or result.get("_shards", {}).get("failed", 0)
        ):
            raise RuntimeError("Elasticsearch late-write cleanup failed")
        guard.ensure_owned()
        logger.warning(
            "Removed interrupted document task vectors: knowledge=%s document=%s deleted=%s",
            knowledge_id,
            document_id,
            result.get("deleted", 0),
        )


__all__ = [
    "DocumentTaskAborted",
    "cleanup_interrupted_task_vectors",
    "ensure_document_active",
    "guard_chunk_mutation",
]
