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
    """删除宿主名下的全部 RAG 消息，返回删除条数。

    有意选择硬删：与图侧 ``delete_all_nodes_by_end_user_id`` 语义对齐，且本模块
    读取路径统一以 ``delete_at`` 不存在为前提；此处不额外过滤软删标记，
    即已标记删除的文档也一并物理清除。
    """
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
            # 版本冲突不进入 failures，只体现在 deleted < total；
            # 此时若当作成功返回，调用方会把残留数据当成已清理。
            raise RuntimeError(
                "delete_by_query incomplete: "
                f"end_user={end_user_id} matched={matched} deleted={deleted} "
                f"version_conflicts={result.get('version_conflicts', 0)}"
            )
        return deleted
    finally:
        await client.close()


async def reassign_end_user_messages(
        source_end_user_ids: Sequence[str],
        target_end_user_id: str,
) -> int:
    """把多个宿主名下的记忆改挂到目标宿主（身份归并），返回更新条数。

    只改挂未软删的文档，与 retrieval/stats/reembedding 的 ``delete_at``
    口径保持一致，避免把源宿主已删除的记录带到目标宿主。

    已知限制：``_rag_memory_id`` 由 (end_user_id, dialog_at, role, content) 派生
    uuid5，同一内容在两个宿主下本来就有不同 ``id``；改挂只换 ``end_user_id``
    字段，因此目标宿主下可能出现同内容的多条记录，不会自动去重。

    非原子：``slices="auto"`` 提升大宿主下的吞吐，但一旦超时/分片失败，
    已落地的部分更新无法回滚；该脚本改挂只依赖 ``end_user_id``，重跑同一批
    source→target 是幂等的，失败后由调用方重试即可收敛。
    """
    source_ids = [str(item) for item in source_end_user_ids if item]
    if not source_ids:
        return 0

    client = AsyncElasticsearch(**build_elasticsearch_client_config())
    try:
        result = await client.update_by_query(
            index=INDEX_NAME,
            query={
                "bool": {
                    "filter": [{"terms": {"end_user_id": source_ids}}],
                    "must_not": [{"exists": {"field": "delete_at"}}],
                }
            },
            script={
                "lang": "painless",
                "source": "ctx._source.end_user_id = params.target",
                "params": {"target": target_end_user_id},
            },
            conflicts="proceed",
            refresh=True,
            slices="auto",
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
            # 版本冲突不进入 failures，只体现在 updated < total；
            # 若当作成功返回，源宿主残留消息会成为孤儿，且归并不可幂等补齐。
            raise RuntimeError(
                "update_by_query incomplete: "
                f"sources={source_ids} target={target_end_user_id} "
                f"matched={int(result.get('total', 0) or 0)} updated={updated} "
                f"version_conflicts={result.get('version_conflicts', 0)}"
            )
        return updated
    finally:
        await client.close()


__all__ = [
    "INDEX_NAME",
    "delete_messages_by_end_user",
    "reassign_end_user_messages",
]
