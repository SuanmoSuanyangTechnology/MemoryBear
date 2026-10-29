"""RAG 记忆向量重算。

图侧 reembedding 引擎的 ``scan_nodes`` 固定读 Neo4j、写入走图写路由，
不能用于 ES-only 的 ``RAG_MEMORY``，因此这里提供同构的独立实现：

- 扫描：``search_after`` 按 ``dialog_at, id`` 升序翻页，只取未软删消息
- 写入：``bulk`` 部分更新维度后缀向量字段（与写入链一致）
- 断点：复用 ``job_state`` 的按 label 游标，失败页不推进游标，重试可续
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk

from app.core.config import settings
from app.core.memory.exceptions import JobSupersededError
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.provider.base import SCAN_PAGE_SIZE
from app.core.memory.storage.provider.elasticsearch.config import (
    build_elasticsearch_client_config,
)
from app.core.memory.storage.provider.elasticsearch.index import get_index_name
from app.core.memory.storage.provider.elasticsearch.index.definitions import (
    get_embedding_field_name,
    is_supported_embedding_dimension,
)
from app.core.memory.storage_services.reembedding_engine import job_state
from app.core.memory.storage_services.reembedding_engine.rebuilder import (
    EndUserRebuildResult,
)

logger = logging.getLogger(__name__)

INDEX_NAME = get_index_name(MemoryNodeType.RAG_MEMORY)
LABEL_KEY = MemoryNodeType.RAG_MEMORY.value
_TEXT_FIELD = "content"
_SOURCE_FIELDS = ("id", "content", "dialog_at")
_SORT: list[dict[str, Any]] = [
    {"dialog_at": {"order": "asc", "missing": "_last"}},
    {"id": {"order": "asc"}},
]


def _base_query(end_user_id: str) -> dict[str, Any]:
    return {
        "bool": {
            "filter": [
                {"term": {"end_user_id": end_user_id}},
                {"bool": {"must_not": [{"exists": {"field": "delete_at"}}]}},
            ]
        }
    }


async def scan_rag_messages(
        client: AsyncElasticsearch,
        end_user_id: str,
        *,
        cursor: str | None = None,
        limit: int = SCAN_PAGE_SIZE,
) -> tuple[list[dict], str | None]:
    """读取一页待重算消息，返回 ``(nodes, next_cursor)``。"""
    search_after = json.loads(cursor) if cursor else None
    result = await client.search(
        index=INDEX_NAME,
        query=_base_query(end_user_id),
        size=limit,
        sort=_SORT,
        search_after=search_after,
        _source=list(_SOURCE_FIELDS),
    )
    hits = (result.get("hits") or {}).get("hits", [])
    nodes = []
    for hit in hits:
        # _source 异常返回（缺失/非 dict，如代理故障时的 list）时按空处理：
        # 该条因缺 content 会被后续 _pending_pairs 自然跳过，避免单条脏数据
        # 抛 AttributeError 被归为「scan 失败」而阻塞游标重试整个页面
        source = hit.get("_source")
        source = source if isinstance(source, dict) else {}
        nodes.append(
            {
                "id": source.get("id") or hit.get("_id"),
                "content": source.get(_TEXT_FIELD),
            }
        )
    last_sort = hits[-1].get("sort") if hits else None
    next_cursor = json.dumps(last_sort) if (len(hits) >= limit and last_sort) else None
    return nodes, next_cursor


async def update_rag_embeddings(
        client: AsyncElasticsearch,
        updates: Sequence[tuple[str, Sequence[float]]],
        dimension: int,
) -> tuple[int, int]:
    """批量覆盖写向量字段，返回 (成功条数, 失败条数)。

    不把整页任何一条失败都升级为异常：``raise_on_error=False`` 时单条失败
    （如文档已被物理删除、版本冲突）不应导致调用方丢弃整页已成功的写入并
    重新向量化。仅统计失败条数，交给上层决定是否继续/重试。
    """
    if not updates:
        return 0, 0
    field = get_embedding_field_name(MemoryNodeType.RAG_MEMORY, dimension)
    actions = [
        {
            "_op_type": "update",
            "_index": INDEX_NAME,
            "_id": node_id,
            "doc": {field: list(vector)},
        }
        for node_id, vector in updates
    ]
    success, errors = await async_bulk(
        client,
        actions,
        refresh="wait_for",
        raise_on_error=False,
    )
    if errors:
        logger.warning(
            "rag re-embed bulk update partial failure: failed=%s success=%s",
            len(errors),
            success,
        )
    return int(success), len(errors)


def _pending_pairs(nodes: list[dict]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for node in nodes:
        node_id = node.get("id")
        text = node.get(_TEXT_FIELD)
        if node_id is None or not isinstance(text, str) or not text.strip():
            continue
        pairs.append((str(node_id), text))
    return pairs


async def _embed_texts(embedder, texts: list[str]) -> tuple[list[list[float]], int]:
    batch_size = settings.EMBEDDING_BATCH_SIZE
    vectors: list[list[float]] = []
    dimension = 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        batch_vectors = await embedder.aembed_documents(batch)
        if len(batch_vectors) != len(batch):
            raise ValueError(
                f"embedder returned {len(batch_vectors)} vectors for {len(batch)} texts"
            )
        for vector in batch_vectors:
            if not vector:
                raise ValueError("embedder returned an empty vector")
            if dimension == 0:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise ValueError("embedder returned inconsistent vector dimensions")
        vectors.extend(batch_vectors)
    if texts and dimension == 0:
        raise ValueError("embedder returned zero-dimension vectors for all texts")
    return vectors, dimension


async def rebuild_rag_end_user_vectors(
        *,
        embedder,
        job_id: str,
        end_user_id: str,
        should_continue: Callable[[], bool] | None = None,
) -> EndUserRebuildResult:
    """重算单个 end_user 的 RAG 记忆向量。"""
    processed = 0
    failed = 0
    incomplete = False
    first_error: str | None = None

    cursor = job_state.get_label_cursor(job_id, end_user_id, LABEL_KEY)
    client = AsyncElasticsearch(**build_elasticsearch_client_config())
    try:
        while True:
            if should_continue is not None and not should_continue():
                raise JobSupersededError(
                    f"job {job_id} no longer owns the rebuild"
                )
            try:
                nodes, next_cursor = await scan_rag_messages(
                    client,
                    end_user_id,
                    cursor=cursor,
                )
            except Exception as exc:
                logger.error(
                    "rag re-embed scan failed: job=%s end_user=%s cursor=%s error=%s",
                    job_id,
                    end_user_id,
                    cursor,
                    exc,
                    exc_info=True,
                )
                incomplete = True
                first_error = first_error or f"scan rag_memory: {exc}"
                break

            if not nodes:
                break

            pairs = _pending_pairs(nodes)
            if pairs:
                try:
                    vectors, dimension = await _embed_texts(
                        embedder,
                        [text for _, text in pairs],
                    )
                    if not is_supported_embedding_dimension(dimension):
                        raise ValueError(
                            "embedding dimension "
                            f"{dimension} is not supported by the "
                            f"{MemoryNodeType.RAG_MEMORY.value} index"
                        )
                    success, failed_nodes = await update_rag_embeddings(
                        client,
                        [
                            (node_id, vector)
                            for (node_id, _), vector in zip(pairs, vectors)
                        ],
                        dimension,
                    )
                    processed += success
                    if failed_nodes:
                        failed += failed_nodes
                        # 单条 bulk 失败（文档已被物理删除、版本冲突）多为永久失败：
                        # 若把整页钉在同一游标上，重试会一直重做该页，该用户永远
                        # 到不了 done，语义检索被永久降级。记录失败后继续推进游标，
                        # 只有 scan / 向量化失败才阻塞游标。
                        logger.error(
                            "rag re-embed bulk partial failure, skip failed docs: "
                            "job=%s end_user=%s failed=%s",
                            job_id,
                            end_user_id,
                            failed_nodes,
                        )
                except Exception as exc:
                    logger.error(
                        "rag re-embed page failed: job=%s end_user=%s nodes=%s error=%s",
                        job_id,
                        end_user_id,
                        len(pairs),
                        exc,
                        exc_info=True,
                    )
                    failed += len(pairs)
                    incomplete = True
                    first_error = first_error or f"write rag_memory: {exc}"
                    break

            if next_cursor is None:
                break
            job_state.set_label_cursor(job_id, end_user_id, LABEL_KEY, next_cursor)
            cursor = next_cursor
    finally:
        await client.close()

    if not incomplete:
        job_state.clear_label_cursors(job_id, end_user_id)

    return EndUserRebuildResult(
        processed_nodes=processed,
        failed_nodes=failed,
        incomplete=incomplete,
        error=first_error,
    )


__all__ = [
    "INDEX_NAME",
    "rebuild_rag_end_user_vectors",
    "scan_rag_messages",
    "update_rag_embeddings",
]
