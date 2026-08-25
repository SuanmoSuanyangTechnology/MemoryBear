"""Compatible knowledge command dispatch without task execution bodies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.owned import Document, File
from ..rag.knowledge_graph.config import is_graph_enabled
from .document import PARSE_TASK_KEY, PARSE_TASK_TTL, _release_parse_claim


@dataclass(frozen=True)
class ReparseSnapshot:
    knowledge_id: uuid.UUID
    document_id: uuid.UUID
    file_key: str
    file_name: str
    is_qa: bool


async def load_reparse_snapshots(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
) -> tuple[list[ReparseSnapshot], int]:
    result = await db.execute(
        select(Document, File)
        .join(File, Document.file_id == File.id)
        .where(Document.kb_id == knowledge_id, Document.status == 1)
    )
    snapshots = []
    skipped = 0
    for document, file in result.all():
        if not file.file_key:
            skipped += 1
            continue
        snapshots.append(
            ReparseSnapshot(
                knowledge_id=knowledge_id,
                document_id=document.id,
                file_key=file.file_key,
                file_name=file.file_name or document.file_name,
                is_qa=(document.parser_config or {}).get("doc_type") == "qa",
            )
        )
    return snapshots, skipped


async def dispatch_reparse_snapshots(
    redis: Any,
    dispatcher: Any,
    snapshots: list[ReparseSnapshot],
    *,
    skipped: int = 0,
) -> dict[str, int]:
    counts = {"queued": 0, "skipped": skipped, "already_running": 0, "failed": 0}
    for snapshot in snapshots:
        task_key = PARSE_TASK_KEY.format(doc_id=snapshot.document_id)
        try:
            claimed = await redis.set(task_key, "CLAIMED", ex=PARSE_TASK_TTL, nx=True)
        except Exception:
            counts["failed"] += 1
            continue
        if not claimed:
            counts["already_running"] += 1
            continue
        try:
            if snapshot.is_qa:
                task_id = await dispatcher.send(
                    "app.core.rag.tasks.import_qa_chunks",
                    args=[
                        str(snapshot.knowledge_id),
                        str(snapshot.document_id),
                        snapshot.file_name,
                    ],
                    kwargs={"file_key": snapshot.file_key, "clear_parse_task": True},
                    queue="qa_import",
                )
            else:
                task_id = await dispatcher.send(
                    "app.core.rag.tasks.parse_document",
                    args=[snapshot.file_key, snapshot.document_id, snapshot.file_name],
                    queue="document_tasks",
                )
        except Exception:
            counts["failed"] += 1
            await _release_parse_claim(redis, task_key)
            continue
        await redis.set(task_key, task_id, ex=PARSE_TASK_TTL)
        counts["queued"] += 1
    return counts


async def dispatch_sync(dispatcher: Any, knowledge_id: uuid.UUID) -> str:
    return await dispatcher.send(
        "app.core.rag.tasks.sync_knowledge_for_kb",
        args=[str(knowledge_id)],
        queue="document_tasks",
    )


async def dispatch_graph_transition(
    dispatcher: Any,
    knowledge_id: uuid.UUID,
    previous_enabled: bool,
    parser_config: dict[str, Any] | None,
) -> str | None:
    current_enabled = is_graph_enabled(parser_config)
    if current_enabled == previous_enabled:
        return None
    if not current_enabled:
        return await dispatcher.send(
            "app.core.rag.tasks.clear_all_knowledge_graph_data",
            args=[str(knowledge_id)],
            queue="graphrag_tasks",
        )
    return await dispatcher.send(
        "app.core.rag.tasks.rebuild_evidence_graph_knowledge",
        args=[str(knowledge_id)],
        queue="graphrag_tasks",
    )


__all__ = [
    "ReparseSnapshot",
    "dispatch_graph_transition",
    "dispatch_reparse_snapshots",
    "dispatch_sync",
    "load_reparse_snapshots",
]
