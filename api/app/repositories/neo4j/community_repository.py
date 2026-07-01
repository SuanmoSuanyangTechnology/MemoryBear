"""Community 节点仓库

管理 Neo4j 中 Community 节点及 BELONGS_TO_COMMUNITY 边的 CRUD 操作。
"""

import asyncio
import logging
from typing import Dict, List, Optional

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import (
    COMMUNITY_NODE_UPSERT,
    ENTITY_JOIN_COMMUNITY,
    ENTITY_LEAVE_ALL_COMMUNITIES,
    GET_ENTITY_NEIGHBORS,
    GET_ALL_ENTITIES_FOR_USER,
    GET_ENTITY_COUNT_FOR_USER,
    GET_ALL_ENTITY_IDS_FOR_USER,
    GET_ENTITIES_PAGE,
    GET_COMMUNITY_MEMBERS,
    GET_COMMUNITY_RELATIONSHIPS,
    BATCH_ASSIGN_ENTITIES_TO_COMMUNITIES,
    GET_COMMUNITY_AVG_EMBEDDINGS_BATCH,
    GET_ENTITY_NEIGHBORS_BATCH_FOR_IDS,
    CHECK_USER_HAS_COMMUNITIES,
    UPDATE_COMMUNITY_MEMBER_COUNT,
    UPDATE_COMMUNITY_METADATA,
    GET_INCOMPLETE_COMMUNITIES,
    GET_INCOMPLETE_COMMUNITIES_WITH_EMBEDDING,
    CHECK_COMMUNITY_IS_COMPLETE,
    CHECK_COMMUNITY_IS_COMPLETE_WITH_EMBEDDING,
    BATCH_UPDATE_COMMUNITY_METADATA,
)

logger = logging.getLogger(__name__)

# 批量分配实体到社区时的单事务分片大小。
# 控制单个 UNWIND 事务最多锁住的实体节点数，避免与同用户写入流水线
# 形成大事务锁竞争 / 死锁。500 兼顾往返削减与锁粒度，锁竞争严重时不应调大。
COMMUNITY_ASSIGN_CHUNK_SIZE = 500
# 单个分片命中 Neo4j 死锁时的最大重试次数。
_DEADLOCK_MAX_RETRY = 3


class CommunityRepository:
    def __init__(self, connector: Neo4jConnector):
        self.connector = connector

    async def upsert_community(
        self, community_id: str, end_user_id: str, member_count: int = 0
    ) -> Optional[str]:
        """创建或更新 Community 节点，返回 community_id。"""
        try:
            result = await self.connector.execute_query(
                COMMUNITY_NODE_UPSERT,
                community_id=community_id,
                end_user_id=end_user_id,
                member_count=member_count,
            )
            return result[0]["community_id"] if result else None
        except Exception as e:
            logger.error(f"upsert_community failed: {e}")
            return None

    async def assign_entity_to_community(
        self, entity_id: str, community_id: str, end_user_id: str
    ) -> bool:
        """将实体关联到社区（先解除旧关联，再建立新关联）。"""
        try:
            await self.connector.execute_query(
                ENTITY_LEAVE_ALL_COMMUNITIES,
                entity_id=entity_id,
                end_user_id=end_user_id,
            )
            result = await self.connector.execute_query(
                ENTITY_JOIN_COMMUNITY,
                entity_id=entity_id,
                community_id=community_id,
                end_user_id=end_user_id,
            )
            return bool(result)
        except Exception as e:
            logger.error(f"assign_entity_to_community failed: {e}")
            return False

    async def get_entity_neighbors(
        self, entity_id: str, end_user_id: str
    ) -> List[Dict]:
        """查询实体的直接邻居及其社区归属。"""
        try:
            return await self.connector.execute_query(
                GET_ENTITY_NEIGHBORS,
                entity_id=entity_id,
                end_user_id=end_user_id,
            )
        except Exception as e:
            logger.error(f"get_entity_neighbors failed: {e}")
            return []

    async def get_all_entities(self, end_user_id: str) -> List[Dict]:
        """拉取某用户下所有实体及其当前社区归属。"""
        try:
            return await self.connector.execute_query(
                GET_ALL_ENTITIES_FOR_USER,
                end_user_id=end_user_id,
            )
        except Exception as e:
            logger.error(f"get_all_entities failed: {e}")
            return []

    async def get_entity_count(self, end_user_id: str) -> int:
        """仅返回用户实体总数，不加载实体数据。"""
        try:
            result = await self.connector.execute_query(
                GET_ENTITY_COUNT_FOR_USER,
                end_user_id=end_user_id,
            )
            return result[0]["entity_count"] if result else 0
        except Exception as e:
            logger.error(f"get_entity_count failed: {e}")
            return 0

    async def get_all_entity_ids(self, end_user_id: str) -> List[str]:
        """仅返回用户所有实体 ID 列表，不加载 embedding 等大字段。"""
        try:
            result = await self.connector.execute_query(
                GET_ALL_ENTITY_IDS_FOR_USER,
                end_user_id=end_user_id,
            )
            return [r["id"] for r in result]
        except Exception as e:
            logger.error(f"get_all_entity_ids failed: {e}")
            return []

    async def get_entities_page(
        self, end_user_id: str, skip: int, limit: int
    ) -> List[Dict]:
        """分页拉取实体，用于全量聚类分批处理。"""
        try:
            return await self.connector.execute_query(
                GET_ENTITIES_PAGE,
                end_user_id=end_user_id,
                skip=skip,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"get_entities_page failed: {e}")
            return []

    async def get_entity_neighbors_for_ids(
        self, entity_ids: List[str], end_user_id: str
    ) -> Dict[str, List[Dict]]:
        """批量拉取指定实体列表的邻居，返回 {entity_id: [neighbors]}。"""
        try:
            rows = await self.connector.execute_query(
                GET_ENTITY_NEIGHBORS_BATCH_FOR_IDS,
                entity_ids=entity_ids,
                end_user_id=end_user_id,
            )
            result: Dict[str, List[Dict]] = {}
            for row in rows:
                eid = row["entity_id"]
                neighbor = {k: v for k, v in row.items() if k != "entity_id"}
                result.setdefault(eid, []).append(neighbor)
            return result
        except Exception as e:
            logger.error(f"get_entity_neighbors_for_ids failed: {e}")
            return {}

    async def get_community_members(
        self, community_id: str, end_user_id: str
    ) -> List[Dict]:
        """查询社区成员列表（含 example 字段）。"""
        try:
            return await self.connector.execute_query(
                GET_COMMUNITY_MEMBERS,
                community_id=community_id,
                end_user_id=end_user_id,
            )
        except Exception as e:
            logger.error(f"get_community_members failed: {e}")
            return []

    async def get_community_relationships(
        self, community_id: str, end_user_id: str
    ) -> List[Dict]:
        """查询社区内实体间的关系三元组（subject, predicate, object）。"""
        try:
            return await self.connector.execute_query(
                GET_COMMUNITY_RELATIONSHIPS,
                community_id=community_id,
                end_user_id=end_user_id,
            )
        except Exception as e:
            logger.error(f"get_community_relationships failed: {e}")
            return []

    async def batch_assign_entities_to_communities(
        self, assignments: List[Dict], end_user_id: str,
        chunk_size: int = COMMUNITY_ASSIGN_CHUNK_SIZE,
    ) -> bool:
        """批量将实体分配到社区（UNWIND + chunk 分片提交）。

        分片提交：单事务最多锁 chunk_size 个实体节点，提交后立即释放，
        避免与同用户写入流水线形成大事务锁竞争 / 死锁。单个分片命中死锁时
        退避重试，失败只影响该分片，不会让整批回滚重来。

        Args:
            assignments: [{"entity_id": str, "community_id": str}, ...]
            end_user_id: 用户 ID
            chunk_size: 单批写入实体数（默认 COMMUNITY_ASSIGN_CHUNK_SIZE）

        Returns:
            True 表示全部分片成功；任一分片最终失败返回 False（不抛出异常）。
        """
        if not assignments:
            return True

        ok = True
        for start in range(0, len(assignments), chunk_size):
            chunk = assignments[start:start + chunk_size]
            label = f"chunk=[{start}:{start + len(chunk)}]"

            # 分片级重试：默认只跑一次（末尾的 break），
            # 仅在"死锁 + 还有重试次数"时通过 continue 再来一轮。
            for attempt in range(1, _DEADLOCK_MAX_RETRY + 1):
                try:
                    await self.connector.execute_query(
                        BATCH_ASSIGN_ENTITIES_TO_COMMUNITIES,
                        assignments=chunk,
                        end_user_id=end_user_id,
                    )
                except Exception as e:
                    # 唯一可重试路径：死锁 & 未耗尽次数 → 退避后进入下一轮
                    if "deadlock" in str(e).lower() and attempt < _DEADLOCK_MAX_RETRY:
                        logger.warning(
                            f"batch_assign {label} 死锁重试 "
                            f"({attempt + 1}/{_DEADLOCK_MAX_RETRY})"
                        )
                    # 退避等待对方事务释放锁，避免立刻重试再次撞进同一个死锁    
                        await asyncio.sleep(0.2 * attempt) # 
                        continue
                    # 终态失败（非死锁 或 重试耗尽）：只影响本分片，继续下一片
                    logger.error(f"batch_assign {label} 失败: {e}")
                    ok = False
                # 成功 或 终态失败 都到这里跳出重试循环
                break
        return ok

    async def get_community_avg_embeddings_batch(
        self, community_ids: List[str], end_user_id: str
    ) -> Dict[str, Dict]:
        """批量计算各社区的平均 name_embedding（Neo4j 侧聚合，无需拉取全量成员）。

        Returns:
            {community_id: {"member_count": int, "avg_embedding": list[float]}}
            若某社区无带 embedding 的成员，则该 community_id 不在返回字典中。
        """
        if not community_ids:
            return {}
        try:
            rows = await self.connector.execute_query(
                GET_COMMUNITY_AVG_EMBEDDINGS_BATCH,
                community_ids=community_ids,
                end_user_id=end_user_id,
            )
            result: Dict[str, Dict] = {}
            for row in rows:
                result[row["cid"]] = {
                    "member_count": row["member_count"],
                    "avg_embedding": row["avg_embedding"],
                }
            return result
        except Exception as e:
            logger.error(f"get_community_avg_embeddings_batch failed: {e}")
            return {}

    async def has_communities(self, end_user_id: str) -> bool:
        """检查该用户是否已有 Community 节点（用于判断全量 vs 增量）。"""
        try:
            result = await self.connector.execute_query(
                CHECK_USER_HAS_COMMUNITIES,
                end_user_id=end_user_id,
            )
            return result[0]["community_count"] > 0 if result else False
        except Exception as e:
            logger.error(f"has_communities failed: {e}")
            return False

    async def refresh_member_count(
        self, community_id: str, end_user_id: str
    ) -> int:
        """重新统计并更新社区成员数，返回最新数量。"""
        try:
            result = await self.connector.execute_query(
                UPDATE_COMMUNITY_MEMBER_COUNT,
                community_id=community_id,
                end_user_id=end_user_id,
            )
            return result[0]["member_count"] if result else 0
        except Exception as e:
            logger.error(f"refresh_member_count failed: {e}")
            return 0

    async def get_incomplete_communities(self, end_user_id: str, check_embedding: bool = False) -> List[str]:
        """查询该用户下属性不完整的 Community 节点 ID 列表。

        Args:
            end_user_id: 用户 ID
            check_embedding: 为 True 时额外检查 summary_embedding 是否缺失（仅当用户有 embedding 模型配置时传 True）
        """
        try:
            query = GET_INCOMPLETE_COMMUNITIES_WITH_EMBEDDING if check_embedding else GET_INCOMPLETE_COMMUNITIES
            result = await self.connector.execute_query(query, end_user_id=end_user_id)
            return [row["community_id"] for row in result]
        except Exception as e:
            logger.error(f"get_incomplete_communities failed: {e}")
            return []

    async def is_community_complete(self, community_id: str, end_user_id: str, check_embedding: bool = False) -> bool:
        """检查单个社区节点的属性是否完整。"""
        try:
            query = CHECK_COMMUNITY_IS_COMPLETE_WITH_EMBEDDING if check_embedding else CHECK_COMMUNITY_IS_COMPLETE
            result = await self.connector.execute_query(query, community_id=community_id, end_user_id=end_user_id)
            return result[0]["is_complete"] if result else False
        except Exception as e:
            logger.error(f"is_community_complete failed: {e}")
            return False

    async def update_community_metadata(
        self,
        community_id: str,
        end_user_id: str,
        name: str,
        summary: str,
        core_entities: List[str],
        summary_embedding: Optional[List[float]] = None,
    ) -> bool:
        """更新社区的名称、摘要、核心实体列表及 summary_embedding。"""
        try:
            result = await self.connector.execute_query(
                UPDATE_COMMUNITY_METADATA,
                community_id=community_id,
                end_user_id=end_user_id,
                name=name,
                summary=summary,
                core_entities=core_entities,
                summary_embedding=summary_embedding,
            )
            return bool(result)
        except Exception as e:
            logger.error(f"update_community_metadata failed: {e}", exc_info=True)
            return False

    async def batch_update_community_metadata(
        self,
        communities: List[Dict],
    ) -> bool:
        """批量更新多个社区的元数据。

        Args:
            communities: 每项包含 community_id, end_user_id, name, summary,
                         core_entities, summary_embedding
        """
        if not communities:
            return True
        try:
            await self.connector.execute_query(
                BATCH_UPDATE_COMMUNITY_METADATA,
                communities=communities,
            )
            return True
        except Exception as e:
            logger.error(f"batch_update_community_metadata failed: {e}")
            return False
