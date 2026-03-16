"""Community 节点仓库

管理 Neo4j 中 Community 节点及 BELONGS_TO_COMMUNITY 边的 CRUD 操作。
"""

import logging
from typing import Dict, List, Optional

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import (
    COMMUNITY_NODE_UPSERT,
    ENTITY_JOIN_COMMUNITY,
    ENTITY_LEAVE_ALL_COMMUNITIES,
    GET_ENTITY_NEIGHBORS,
    GET_ALL_ENTITIES_FOR_USER,
    GET_ENTITIES_PAGE,
    GET_COMMUNITY_MEMBERS,
    GET_ALL_COMMUNITY_MEMBERS_BATCH,
    GET_ALL_ENTITY_NEIGHBORS_BATCH,
    GET_ENTITY_NEIGHBORS_BATCH_FOR_IDS,
    CHECK_USER_HAS_COMMUNITIES,
    UPDATE_COMMUNITY_MEMBER_COUNT,
    UPDATE_COMMUNITY_METADATA,
)

logger = logging.getLogger(__name__)


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
        """查询社区成员列表。"""
        try:
            return await self.connector.execute_query(
                GET_COMMUNITY_MEMBERS,
                community_id=community_id,
                end_user_id=end_user_id,
            )
        except Exception as e:
            logger.error(f"get_community_members failed: {e}")
            return []

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

    async def update_community_metadata(
        self,
        community_id: str,
        end_user_id: str,
        name: str,
        summary: str,
        core_entities: List[str],
        summary_embedding: Optional[List[float]] = None,
    ) -> bool:
        """更新社区的名称、摘要、核心实体列表和摘要向量。"""
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
            logger.error(f"update_community_metadata failed: {e}")
            return False
