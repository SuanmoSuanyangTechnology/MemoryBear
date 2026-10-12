from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import settings
from app.core.memory.exceptions import JobSupersededError
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    NodeProjection,
)
from app.core.memory.storage.service import MemoryStorageService
from app.core.memory.storage_services.reembedding_engine import job_state
from app.core.memory.storage_services.reembedding_engine.spec import (
    REEMBED_TARGETS,
    ReembedTarget,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EndUserRebuildResult:
    """Counts for one end_user rebuild."""

    processed_nodes: int = 0
    failed_nodes: int = 0
    #: A label stopped short of finishing: its page failed or could not be read.
    #: The cursor deliberately stays before the failed page, so a retry resumes
    #: there instead of skipping it.
    incomplete: bool = False
    #: First failure reason, recorded on the end_user's row.
    error: str | None = None


def _projection_for(target: ReembedTarget) -> NodeProjection:
    return NodeProjection(fields=("id", target.text_field))


def _pending_pairs(
        nodes: list[dict],
        target: ReembedTarget,
) -> list[tuple[str, str]]:
    """Collect ``(node_id, text)`` for nodes that carry embeddable text.

    Nodes without text are skipped, matching the write path which never embeds
    an empty string.
    """
    pairs: list[tuple[str, str]] = []
    for node in nodes:
        node_id = node.get("id")
        text = node.get(target.text_field)
        if node_id is None or not isinstance(text, str) or not text.strip():
            continue
        pairs.append((str(node_id), text))
    return pairs


async def _embed_texts(embedder, texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` in batches, preserving input order.

    单请求条数上限属于 provider，不由调用方想切多大的批决定。调用方自己切片的
    语义是页大小（页内超限由 provider 客户端自己处理），而 DashScope 客户端并
    不切批 —— 所以这里必须按 ``EMBEDDING_BATCH_SIZE`` 切片，与 mem-knowledge
    和写入链的客户端保持同样标准，而不是按调用方想要的页大小 100。

    :raises ValueError: when the embedder returns a different number of vectors
        than requested. Writing those vectors would bind them to the wrong
        nodes, so this is a hard failure rather than a partial write.
    """
    batch_size = settings.EMBEDDING_BATCH_SIZE
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        batch_vectors = await embedder.aembed_documents(batch)
        if len(batch_vectors) != len(batch):
            raise ValueError(
                "embedder returned "
                f"{len(batch_vectors)} vectors for {len(batch)} texts"
            )
        vectors.extend(batch_vectors)
    return vectors


async def rebuild_end_user_vectors(
        *,
        storage: MemoryStorageService,
        embedder,
        job_id: str,
        end_user_id: str,
        should_continue: Callable[[], bool] | None = None,
        targets: tuple[ReembedTarget, ...] = REEMBED_TARGETS,
) -> EndUserRebuildResult:
    processed = 0
    failed = 0
    incomplete = False
    first_error: str | None = None

    for target in targets:
        label = target.label
        cursor = job_state.get_label_cursor(
            job_id,
            end_user_id,
            label.value,
        )
        while True:
            if should_continue is not None and not should_continue():
                raise JobSupersededError(
                    f"job {job_id} no longer owns the rebuild"
                )
            try:
                # 只重算未删除节点：软删节点由恢复路径在恢复时重算向量。
                # 页大小不在此处指定，取存储层 ``scan_nodes`` 的默认值：在这里
                # 另写一个数字，改一处就会让重算的分页与其他扫描调用方分叉。
                nodes, next_cursor = await storage.scan_nodes(
                    label,
                    NodeFilter.all_of(
                        FilterCondition(field="end_user_id", value=end_user_id),
                        FilterCondition(
                            field="delete_at",
                            operator=FilterOperator.EXISTS,
                            value=False,
                        ),
                    ),
                    cursor=cursor,
                    projection=_projection_for(target),
                )
            except Exception as exc:
                logger.error(
                    "memory re-embed scan failed: job=%s end_user=%s "
                    "label=%s cursor=%s error=%s",
                    job_id,
                    end_user_id,
                    label.value,
                    cursor,
                    exc,
                    exc_info=True,
                )
                incomplete = True
                first_error = first_error or f"scan {label.value}: {exc}"
                break

            if not nodes:
                break

            pairs = _pending_pairs(nodes, target)
            if pairs:
                try:
                    vectors = await _embed_texts(
                        embedder,
                        [text for _, text in pairs],
                    )
                    result = await storage.update_node_embeddings(
                        label,
                        target.vector_field,
                        [
                            (node_id, vector)
                            for (node_id, _), vector in zip(pairs, vectors)
                        ],
                    )
                    processed += result.affected_count
                except Exception as exc:
                    logger.error(
                        "memory re-embed page failed: job=%s end_user=%s "
                        "label=%s nodes=%s error=%s",
                        job_id,
                        end_user_id,
                        label.value,
                        len(pairs),
                        exc,
                        exc_info=True,
                    )
                    failed += len(pairs)
                    incomplete = True
                    first_error = first_error or f"write {label.value}: {exc}"
                    # Stop this label *before* the failed page: the cursor below
                    # is not advanced, so a retry redoes exactly these nodes.
                    break

            if next_cursor is None:
                break
            # Only persist the cursor after the page was written: a crash or a
            # failure redoes one page (idempotent) instead of skipping it.
            job_state.set_label_cursor(
                job_id,
                end_user_id,
                label.value,
                next_cursor,
            )
            cursor = next_cursor

    if not incomplete:
        # Cursors may only be dropped for a fully rebuilt end_user; otherwise a
        # retry would restart every label from page 0 and redo finished work.
        job_state.clear_label_cursors(job_id, end_user_id)
    return EndUserRebuildResult(
        processed_nodes=processed,
        failed_nodes=failed,
        incomplete=incomplete,
        error=first_error,
    )


__all__ = [
    "EndUserRebuildResult",
    "JobSupersededError",
    "rebuild_end_user_vectors",
]
