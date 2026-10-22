"""Resolve an active source task before admitting an explicit Wiki generation request."""

import asyncio

from ...rag.wiki.config import WikiSourcePending
from ...repositories.wiki import state
from ...tasks.state import PARSE_TASK_KEY
from .runtime import transaction

SOURCE_TASK_LOOKUP_ATTEMPTS = 4
SOURCE_TASK_LOOKUP_DELAY_SECONDS = 0.1


async def admit_generation(runtime, kb_id, document_id, expected_scope):
    source_task_id = None
    for attempt in range(SOURCE_TASK_LOOKUP_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(
                transaction, runtime, state.admit, kb_id, document_id,
                explicit=True, expected_scope=expected_scope, source_task_id=source_task_id,
            )
        except WikiSourcePending:
            if attempt == SOURCE_TASK_LOOKUP_ATTEMPTS:
                raise
        # Scope and document membership were checked in the completed transaction above.
        redis = await runtime.redis.client()
        value = await redis.get(PARSE_TASK_KEY.format(doc_id=document_id))
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        source_task_id = value if value and value not in {"CLAIMED", "unknown"} else None
        if not source_task_id:
            await asyncio.sleep(SOURCE_TASK_LOOKUP_DELAY_SECONDS)
    raise AssertionError("Wiki admission exhausted without returning or raising")
