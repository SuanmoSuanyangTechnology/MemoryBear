"""RAG 记忆索引的生命周期操作（删除 / 宿主归并）。

与图侧 ``storage.custom`` 的对应能力平行：RAG 记忆只存在于独立索引，
宿主删除与跨渠道归并需要直接改写 ``rag_memory_current``。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from elasticsearch import AsyncElasticsearch

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.provider.elasticsearch.config import (
    build_elasticsearch_client_config,
)
from app.core.memory.storage.provider.elasticsearch.index import get_index_name

logger = logging.getLogger(__name__)

INDEX_NAME = get_index_name(MemoryNodeType.RAG_MEMORY)


def _raise_if_incomplete(result: dict, operation: str) -> None:
    """写入/删除存在分片失败或超时时抛出异常。

    ``conflicts="proceed"`` 会把版本冲突计入 ``failures`` 而不让请求失败，只凭
    ``deleted``/``updated`` 判断会误认为已全部生效，因此这里显式拦截，避免宿主
    删除或身份归并留下未迁移的残留数据。
    """
    if result.get("timed_out"):
        raise RuntimeError(f"{operation} timed out")
    failures = result.get("failures") or []
    if failures:
        raise RuntimeError(
            f"{operation} incomplete: {len(failures)} shard failure(s); "
            f"first={failures[0]}"
        )


async def delete_messages_by_end_user(end_user_id: str) -> int:
    """删除宿主名下的全部 RAG 消息，返回删除条数。"""
    client = AsyncElasticsearch(**build_elasticsearch_client_config())
    try:
        result = await client.delete_by_query(
            index=INDEX_NAME,
            query={"term": {"end_user_id": end_user_id}},
            conflicts="proceed",
            refresh=True,
            slices="auto",
        )
        _raise_if_incomplete(result, "delete_by_query")
        deleted = int(result.get("deleted", 0) or 0)
        matched = int(result.get("total", 0) or 0)
        logger.info(
            "[RAGStorage] 删除宿主记忆完成: end_user=%s matched=%s deleted=%s",
            end_user_id,
            matched,
            deleted,
        )
        if deleted < matched:
            logger.warning(
                "[RAGStorage] 删除未全覆盖: end_user=%s matched=%s deleted=%s "
                "version_conflicts=%s",
                end_user_id,
                matched,
                deleted,
                result.get("version_conflicts", 0),
            )
        return deleted
    finally:
        await client.close()


async def reassign_end_user_messages(
        source_end_user_ids: Sequence[str],
        target_end_user_id: str,
) -> int:
    """把多个宿主名下的记忆改挂到目标宿主（身份归并），返回更新条数。"""
    source_ids = [str(item) for item in source_end_user_ids if item]
    if not source_ids:
        return 0

    client = AsyncElasticsearch(**build_elasticsearch_client_config())
    try:
        result = await client.update_by_query(
            index=INDEX_NAME,
            query={"terms": {"end_user_id": source_ids}},
            script={
                "lang": "painless",
                "source": "ctx._source.end_user_id = params.target",
                "params": {"target": target_end_user_id},
            },
            conflicts="proceed",
            refresh=True,
        )
        updated = int(result.get("updated", 0) or 0)
        logger.info(
            "[RAGStorage] 宿主归并迁移完成: sources=%s target=%s "
            "matched=%s updated=%s",
            source_ids,
            target_end_user_id,
            int(result.get("total", 0) or 0),
            updated,
        )
        _raise_if_incomplete(result, "update_by_query")
        if updated < int(result.get("total", 0) or 0):
            logger.warning(
                "[RAGStorage] 归并未全覆盖: sources=%s target=%s matched=%s "
                "updated=%s version_conflicts=%s",
                source_ids,
                target_end_user_id,
                int(result.get("total", 0) or 0),
                updated,
                result.get("version_conflicts", 0),
            )
        return updated
    finally:
        await client.close()


__all__ = [
    "INDEX_NAME",
    "delete_messages_by_end_user",
    "reassign_end_user_messages",
]
