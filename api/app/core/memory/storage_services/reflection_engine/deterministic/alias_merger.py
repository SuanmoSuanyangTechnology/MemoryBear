"""反思阶段 · 别名归并（"别名属于" 关系处理）

将 (A)-[:EXTRACTED_RELATIONSHIP {predicate:'别名属于'}]->(B) 的别名节点 A
归并进规范实体 B：A.name 进 B.aliases、A.description 拼入 B.description，
A 上其它边重定向到 B，最后删除 A 节点。

三步均为确定性 Cypher，不涉及 LLM / embedding。
逻辑迁移自原写入后处理任务 post_store_dedup_and_alias_merge。
"""
import logging
from typing import Any, Dict

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import (
    MERGE_ALIAS_BELONGS_TO,
    REDIRECT_ALIAS_EDGES,
    DELETE_ALIAS_NODES,
)

logger = logging.getLogger(__name__)


async def merge_alias_belongs_to(
    connector: Neo4jConnector,
    end_user_id: str,
) -> Dict[str, Any]:
    """按 end_user_id 全量处理 "别名属于" 关系。

    顺序执行三步，每步独立异常隔离：
      1. 别名归并：source.name → target.aliases，source.description → target.description
      2. 边重定向：别名节点其它边 → 规范实体
      3. 删除别名节点：DETACH DELETE

    Args:
        connector: Neo4j 连接器
        end_user_id: 终端用户 ID

    Returns:
        统计字典：alias_merged / edges_redirected / alias_nodes_deleted / errors
    """
    result: Dict[str, Any] = {
        "alias_merged": 0,
        "edges_redirected": 0,
        "alias_nodes_deleted": 0,
        "errors": {},
    }

    # ── 1. 别名归并（name 进 aliases，description 拼接） ──
    try:
        records = await connector.execute_query(
            MERGE_ALIAS_BELONGS_TO,
            end_user_id=end_user_id,
        )
        result["alias_merged"] = len(records) if records else 0
        logger.info(
            f"[AliasMerge] 别名归并完成 end_user_id={end_user_id}, "
            f"影响 target={result['alias_merged']}"
        )
    except Exception as e:
        logger.warning(f"[AliasMerge] 别名归并失败 end_user_id={end_user_id}: {e}")
        result["errors"]["merge"] = str(e)

    # ── 2. 边重定向（别名节点其它边 → 规范实体） ──
    try:
        redirect_records = await connector.execute_query(
            REDIRECT_ALIAS_EDGES,
            end_user_id=end_user_id,
        )
        if redirect_records:
            row = redirect_records[0]
            result["edges_redirected"] = (
                (row.get("redirected_incoming") or 0)
                + (row.get("redirected_outgoing") or 0)
                + (row.get("redirected_stmt") or 0)
            )
        logger.info(
            f"[AliasMerge] 边重定向完成 end_user_id={end_user_id}, "
            f"汇总={result['edges_redirected']}"
        )
    except Exception as e:
        logger.warning(f"[AliasMerge] 边重定向失败 end_user_id={end_user_id}: {e}")
        result["errors"]["redirect"] = str(e)

    # ── 3. 删除别名节点（DETACH DELETE） ──
    try:
        delete_records = await connector.execute_query(
            DELETE_ALIAS_NODES,
            end_user_id=end_user_id,
        )
        result["alias_nodes_deleted"] = (
            delete_records[0].get("deleted_count", 0) if delete_records else 0
        )
        logger.info(
            f"[AliasMerge] 别名节点删除完成 end_user_id={end_user_id}, "
            f"删除={result['alias_nodes_deleted']}"
        )
    except Exception as e:
        logger.warning(f"[AliasMerge] 别名节点删除失败 end_user_id={end_user_id}: {e}")
        result["errors"]["delete"] = str(e)

    return result
