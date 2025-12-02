# -*- coding: utf-8 -*-
"""实体仓储模块

本模块提供实体节点的数据访问功能。

Classes:
    EntityRepository: 实体仓储，管理ExtractedEntityNode的CRUD操作
"""

from typing import List, Optional, Dict
from datetime import datetime

from app.repositories.neo4j.base_neo4j_repository import BaseNeo4jRepository
from app.core.memory.models.graph_models import ExtractedEntityNode
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


class EntityRepository(BaseNeo4jRepository[ExtractedEntityNode]):
    """实体仓储
    
    管理实体节点的创建、查询、更新和删除操作。
    提供按类型、名称、向量相似度等条件查询实体的方法。
    
    Attributes:
        connector: Neo4j连接器实例
        node_label: 节点标签，固定为"ExtractedEntity"
    """
    
    def __init__(self, connector: Neo4jConnector):
        """初始化实体仓储
        
        Args:
            connector: Neo4j连接器实例
        """
        super().__init__(connector, "ExtractedEntity")
    
    def _map_to_entity(self, node_data: Dict) -> ExtractedEntityNode:
        """将节点数据映射为实体对象
        
        Args:
            node_data: 从Neo4j查询返回的节点数据字典
            
        Returns:
            ExtractedEntityNode: 实体对象
        """
        # 从查询结果中提取节点数据
        n = node_data.get('n', node_data)
        
        # 处理datetime字段
        if isinstance(n.get('created_at'), str):
            n['created_at'] = datetime.fromisoformat(n['created_at'])
        if n.get('expired_at') and isinstance(n['expired_at'], str):
            n['expired_at'] = datetime.fromisoformat(n['expired_at'])
        
        return ExtractedEntityNode(**n)
    
    async def find_by_type(self, entity_type: str, limit: int = 100) -> List[ExtractedEntityNode]:
        """根据实体类型查询
        
        Args:
            entity_type: 实体类型（如"Person", "Organization"等）
            limit: 返回结果的最大数量
            
        Returns:
            List[ExtractedEntityNode]: 实体列表
        """
        return await self.find({"entity_type": entity_type}, limit=limit)
    
    async def find_by_group_id(self, group_id: str, limit: int = 100) -> List[ExtractedEntityNode]:
        """根据group_id查询实体
        
        Args:
            group_id: 组ID
            limit: 返回结果的最大数量
            
        Returns:
            List[ExtractedEntityNode]: 实体列表
        """
        return await self.find({"group_id": group_id}, limit=limit)
    
    async def find_by_name(
        self,
        name: str,
        group_id: Optional[str] = None,
        limit: int = 100
    ) -> List[ExtractedEntityNode]:
        """根据名称查询实体
        
        支持模糊匹配（CONTAINS）。
        
        Args:
            name: 实体名称
            group_id: 可选的组ID过滤
            limit: 返回结果的最大数量
            
        Returns:
            List[ExtractedEntityNode]: 实体列表
        """
        where_clause = "n.name CONTAINS $name"
        if group_id:
            where_clause += " AND n.group_id = $group_id"
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE {where_clause}
        RETURN n
        LIMIT $limit
        """
        
        params = {"name": name, "limit": limit}
        if group_id:
            params["group_id"] = group_id
        
        results = await self.connector.execute_query(query, **params)
        return [self._map_to_entity(r) for r in results]
    
    async def find_related_entities(
        self,
        entity_id: str,
        relation_type: Optional[str] = None,
        limit: int = 100
    ) -> List[ExtractedEntityNode]:
        """查询相关实体
        
        查询与指定实体有关系的其他实体。
        
        Args:
            entity_id: 实体ID
            relation_type: 可选的关系类型过滤
            limit: 返回结果的最大数量
            
        Returns:
            List[ExtractedEntityNode]: 相关实体列表
        """
        if relation_type:
            query = """
            MATCH (e1:ExtractedEntity {id: $entity_id})-[r:RELATES_TO {relation_type: $relation_type}]->(e2:ExtractedEntity)
            RETURN e2 as n
            LIMIT $limit
            """
            results = await self.connector.execute_query(
                query,
                entity_id=entity_id,
                relation_type=relation_type,
                limit=limit
            )
        else:
            query = """
            MATCH (e1:ExtractedEntity {id: $entity_id})-[r:RELATES_TO]->(e2:ExtractedEntity)
            RETURN e2 as n
            LIMIT $limit
            """
            results = await self.connector.execute_query(
                query,
                entity_id=entity_id,
                limit=limit
            )
        
        return [self._map_to_entity(r) for r in results]
    
    async def search_by_embedding(
        self,
        embedding: List[float],
        group_id: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.7
    ) -> List[Dict]:
        """基于向量相似度搜索实体
        
        使用余弦相似度计算查询向量与实体名称向量的相似度。
        
        Args:
            embedding: 查询向量
            group_id: 可选的组ID过滤
            limit: 返回结果的最大数量
            min_score: 最小相似度分数阈值
            
        Returns:
            List[Dict]: 包含实体和相似度分数的字典列表
                每个字典包含: entity (ExtractedEntityNode), score (float)
        """
        where_clause = "n.name_embedding IS NOT NULL"
        if group_id:
            where_clause += " AND n.group_id = $group_id"
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE {where_clause}
        WITH n, gds.similarity.cosine(n.name_embedding, $embedding) AS score
        WHERE score > $min_score
        RETURN n, score
        ORDER BY score DESC
        LIMIT $limit
        """
        
        params = {
            "embedding": embedding,
            "min_score": min_score,
            "limit": limit
        }
        if group_id:
            params["group_id"] = group_id
        
        results = await self.connector.execute_query(query, **params)
        
        return [
            {
                "entity": self._map_to_entity(r),
                "score": r.get("score", 0.0)
            }
            for r in results
        ]
    
    async def find_by_statement_id(self, statement_id: str) -> List[ExtractedEntityNode]:
        """根据陈述句ID查询实体
        
        查询从指定陈述句中提取的所有实体。
        
        Args:
            statement_id: 陈述句ID
            
        Returns:
            List[ExtractedEntityNode]: 实体列表
        """
        return await self.find({"statement_id": statement_id})
    
    async def find_strong_entities(
        self,
        group_id: str,
        limit: int = 100
    ) -> List[ExtractedEntityNode]:
        """查询强连接的实体
        
        Args:
            group_id: 组ID
            limit: 返回结果的最大数量
            
        Returns:
            List[ExtractedEntityNode]: 强连接的实体列表
        """
        return await self.find(
            {"group_id": group_id, "connect_strength": "Strong"},
            limit=limit
        )
    
    async def get_entity_count_by_type(self, group_id: str) -> Dict[str, int]:
        """统计各类型实体的数量
        
        Args:
            group_id: 组ID
            
        Returns:
            Dict[str, int]: 实体类型到数量的映射
        """
        query = """
        MATCH (n:ExtractedEntity {group_id: $group_id})
        RETURN n.entity_type as entity_type, count(n) as count
        ORDER BY count DESC
        """
        results = await self.connector.execute_query(query, group_id=group_id)
        return {r["entity_type"]: r["count"] for r in results}
    
    async def find_by_config_id(
        self,
        config_id: str,
        limit: int = 100
    ) -> List[ExtractedEntityNode]:
        """根据config_id查询实体
        
        Args:
            config_id: 配置ID
            limit: 返回结果的最大数量
            
        Returns:
            List[ExtractedEntityNode]: 实体列表
        """
        return await self.find({"config_id": config_id}, limit=limit)
    
    async def search_by_embedding_with_config(
        self,
        embedding: List[float],
        config_id: Optional[str] = None,
        group_id: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.7
    ) -> List[Dict]:
        """基于向量相似度搜索实体,可选择按config_id过滤
        
        使用余弦相似度计算查询向量与实体名称向量的相似度。
        支持按config_id过滤结果,确保只返回使用特定配置处理的实体。
        
        Args:
            embedding: 查询向量
            config_id: 可选的配置ID过滤
            group_id: 可选的组ID过滤
            limit: 返回结果的最大数量
            min_score: 最小相似度分数阈值
            
        Returns:
            List[Dict]: 包含实体和相似度分数的字典列表
                每个字典包含: entity (ExtractedEntityNode), score (float)
        """
        # 构建查询条件
        where_clauses = ["n.name_embedding IS NOT NULL"]
        params = {
            "embedding": embedding,
            "min_score": min_score,
            "limit": limit
        }
        
        if config_id:
            where_clauses.append("n.config_id = $config_id")
            params["config_id"] = config_id
        
        if group_id:
            where_clauses.append("n.group_id = $group_id")
            params["group_id"] = group_id
        
        where_str = " AND ".join(where_clauses)
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE {where_str}
        WITH n, gds.similarity.cosine(n.name_embedding, $embedding) AS score
        WHERE score > $min_score
        RETURN n, score
        ORDER BY score DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(query, **params)
        
        return [
            {
                "entity": self._map_to_entity(r),
                "score": r.get("score", 0.0)
            }
            for r in results
        ]
