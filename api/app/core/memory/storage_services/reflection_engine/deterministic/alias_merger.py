"""反思阶段 · 别名归并（"别名属于" 关系处理）

将 (A)-[:EXTRACTED_RELATIONSHIP {predicate:'别名属于'}]->(B) 的别名节点 A
归并进规范实体 B：A.name 进 B.aliases、A.description 拼入 B.description，
A 上其它边重定向到 B，最后删除 A 节点。

三步均为确定性 Cypher，不涉及 LLM / embedding。
逻辑迁移自原写入后处理任务 post_store_dedup_and_alias_merge。
"""
import asyncio
import logging
from typing import Any, Dict, List

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import (
    GET_USER_ENTITY_ALIASES,
    GET_ALIAS_BELONGS_CANDIDATES,
)

from app.core.memory.storage.custom.reflection_mutations import (
    delete_alias_nodes,
    drop_alias_belongs_edges,
    merge_alias_properties,
    redirect_alias_edges,
)
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

logger = logging.getLogger(__name__)


async def collect_alias_candidates(
    connector: Neo4jConnector,
    end_user_id: str,
) -> List[Dict[str, Any]]:
    """收集该 user 全部 "别名属于" 候选（含两端节点上下文）。"""
    records = await connector.execute_query(
        GET_ALIAS_BELONGS_CANDIDATES,
        end_user_id=end_user_id,
    )
    return list(records or [])


async def drop_alias_edges(
    connector: Neo4jConnector,
    end_user_id: str,
    drop_alias_ids: List[str],
    *,
    neo4j_client: Neo4jClient | None = None,
) -> int:
    """删除被判 drop 的 "别名属于" 边（只删边，不删节点）。返回删除条数。"""
    if not drop_alias_ids:
        return 0
    return await drop_alias_belongs_edges(
        end_user_id,
        drop_alias_ids,
        neo4j_client=neo4j_client,
    )


async def merge_alias_belongs_to(
    connector: Neo4jConnector,
    end_user_id: str,
    alias_ids: List[str],
    *,
    neo4j_client: Neo4jClient | None = None,
) -> Dict[str, Any]:
    """按 end_user_id 全量处理 "别名属于" 关系。

    顺序执行三步，每步一个独立的 storage 事务、独立的异常隔离：
      1. 别名归并：source.name → target.aliases，source.description → target.description
      2. 边重定向：别名节点其它边 → 规范实体
      3. 删除别名节点：DETACH DELETE

    某步失败只记 warning 并写入 ``errors``，不回滚其它步骤、不跳过后续步骤，
    第 4 步 PG 同步照常执行。第 1、3 步各自登记节点 Outbox 事件，
    第 2 步是纯关系写、不发事件。

    归并完成后，再将用户实体的最新 aliases 同步回 PostgreSQL end_user_info
    （aliases 增量合并、other_name 为空时取 aliases[0]、同步 end_user.other_name）。

    Args:
        connector: Neo4j 连接器
        end_user_id: 终端用户 ID
        alias_ids: 判定通过、需要归并的别名节点业务 ID
        neo4j_client: 本轮复用的 storage Neo4j client；缺省时下层自建并即用即关

    Returns:
        统计字典：alias_merged / edges_redirected / alias_nodes_deleted / pg_synced / errors
    """
    result: Dict[str, Any] = {
        "alias_merged": 0,
        "edges_redirected": 0,
        "alias_nodes_deleted": 0,
        "pg_synced": False,
        "errors": {},
    }

    # ── 1~3 步仅在有 merge 候选时执行（按 alias_ids 筛选）──
    if not alias_ids:
        logger.debug(f"[AliasMerge] merge 集合为空，跳过归并三步 end_user_id={end_user_id}")
    else:
        # ── 1. 别名归并（name 进 aliases，description 拼接） ──
        try:
            result["alias_merged"] = await merge_alias_properties(
                end_user_id, alias_ids, neo4j_client=neo4j_client
            )
            logger.info(
                f"[AliasMerge] 别名归并完成 end_user_id={end_user_id}, "
                f"影响 target={result['alias_merged']}"
            )
        except Exception as e:
            logger.warning(f"[AliasMerge] 别名归并失败 end_user_id={end_user_id}: {e}")
            result["errors"]["merge"] = str(e)

        # ── 2. 边重定向（别名节点其它边 → 规范实体） ──
        try:
            result["edges_redirected"] = await redirect_alias_edges(
                end_user_id, alias_ids, neo4j_client=neo4j_client
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
            result["alias_nodes_deleted"] = await delete_alias_nodes(
                end_user_id, alias_ids, neo4j_client=neo4j_client
            )
            logger.info(
                f"[AliasMerge] 别名节点删除完成 end_user_id={end_user_id}, "
                f"删除={result['alias_nodes_deleted']}"
            )
        except Exception as e:
            logger.warning(f"[AliasMerge] 别名节点删除失败 end_user_id={end_user_id}: {e}")
            result["errors"]["delete"] = str(e)

    # ── 4. 同步用户实体最新 aliases 到 PostgreSQL end_user_info ──
    try:
        records = await connector.execute_query(
            GET_USER_ENTITY_ALIASES,
            end_user_id=end_user_id,
        )
        # 汇总所有用户实体节点的 aliases（去重，忽略大小写），交给 PG 增量合并
        merged_aliases: List[str] = []
        seen_lower = set()
        for rec in records or []:
            for alias in rec.get("aliases") or []:
                alias = (alias or "").strip()
                if alias and alias.lower() not in seen_lower:
                    merged_aliases.append(alias)
                    seen_lower.add(alias.lower())

        if merged_aliases:
            # PG 为同步 session，放到线程池执行，避免阻塞反思的 event loop
            await asyncio.to_thread(
                _sync_user_aliases_to_pg, end_user_id, merged_aliases
            )
            result["pg_synced"] = True
            logger.info(
                f"[AliasMerge] aliases 同步 PostgreSQL 完成 end_user_id={end_user_id}, "
                f"aliases_count={len(merged_aliases)}"
            )
        else:
            logger.debug(
                f"[AliasMerge] 用户实体无 aliases，跳过 PG 同步 end_user_id={end_user_id}"
            )
    except Exception as e:
        logger.warning(f"[AliasMerge] aliases 同步 PostgreSQL 失败 end_user_id={end_user_id}: {e}")
        result["errors"]["pg_sync"] = str(e)

    return result


def _sync_user_aliases_to_pg(end_user_id: str, aliases: List[str]) -> None:
    """将别名归并后的用户 aliases 增量同步到 PostgreSQL end_user_info。

    与写入阶段 tasks._sync_end_user_info_pg 行为一致：
      - aliases 合并到 end_user_info.aliases（去重，忽略大小写）
      - end_user_info.other_name 为空时取 aliases[0]
      - end_user.other_name 与 end_user_info.other_name 保持同步（仅当 end_user 为空）

    失败只记日志，不抛异常，不影响反思主流程。
    """
    import uuid as _uuid

    from app.db import get_db_context
    from app.repositories.end_user_info_repository import EndUserInfoRepository
    from app.repositories.end_user_repository import EndUserRepository

    try:
        eu_uuid = _uuid.UUID(end_user_id)
    except (ValueError, TypeError):
        logger.warning(f"[AliasMerge] 非法 end_user_id，跳过 PG 同步: {end_user_id}")
        return

    with get_db_context() as db:
        info_repo = EndUserInfoRepository(db)
        info = info_repo.update_aliases_and_metadata(
            end_user_id=eu_uuid,
            new_aliases=aliases,
            new_metadata=None,
        )
        if info is None:
            logger.warning(
                f"[AliasMerge] end_user_info 记录不存在，跳过 PG 同步: end_user_id={end_user_id}"
            )
            return

        # 同步 end_user.other_name（仅当 end_user 侧为空）
        new_other_name = (info.other_name or "").strip()
        if new_other_name:
            eu_repo = EndUserRepository(db)
            end_user = eu_repo.get_end_user_by_id(eu_uuid)
            if end_user and not (end_user.other_name or "").strip():
                end_user.other_name = new_other_name
                db.commit()
                logger.info(
                    f"[AliasMerge] 同步 end_user.other_name={new_other_name}: "
                    f"end_user_id={end_user_id}"
                )
