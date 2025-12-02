# -*- coding: utf-8 -*-
"""陈述句仓储模块

本模块提供陈述句节点的数据访问功能。

Classes:
    StatementRepository: 陈述句仓储，管理StatementNode的CRUD操作
"""

from typing import List, Optional, Dict
from datetime import datetime

from app.repositories.neo4j.base_neo4j_repository import BaseNeo4jRepository
from app.core.memory.models.graph_models import StatementNode
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.utils.data.ontology import TemporalInfo


class StatementRepository(BaseNeo4jRepository[StatementNode]):
    """陈述句仓储
    
    管理陈述句节点的创建、查询、更新和删除操作。
    提供按chunk_id、group_id、向量相似度等条件查询陈述句的方法。
    
    Attributes:
        connector: Neo4j连接器实例
        node_label: 节点标签，固定为"Statement"
    """
    
    def __init__(self, connector: Neo4jConnector):
        """初始化陈述句仓储
        
        Args:
            connector: Neo4j连接器实例
        """
        super().__init__(connector, "Statement")
    
    def _map_to_entity(self, node_data: Dict) -> StatementNode:
        """将节点数据映射为陈述句实体
        
        Args:
            node_data: 从Neo4j查询返回的节点数据字典
            
        Returns:
            StatementNode: 陈述句实体对象
        """
        # 从查询结果中提取节点数据
        n = node_data.get('n', node_data)
        
        # 处理datetime字段
        if isinstance(n.get('created_at'), str):
            n['created_at'] = datetime.fromisoformat(n['created_at'])
        if n.get('expired_at') and isinstance(n['expired_at'], str):
            n['expired_at'] = datetime.fromisoformat(n['expired_at'])
        if n.get('valid_at') and isinstance(n['valid_at'], str):
            n['valid_at'] = datetime.fromisoformat(n['valid_at'])
        if n.get('invalid_at') and isinstance(n['invalid_at'], str):
            n['invalid_at'] = datetime.fromisoformat(n['invalid_at'])
        
        # 处理temporal_info字段
        if isinstance(n.get('temporal_info'), dict):
            n['temporal_info'] = TemporalInfo(**n['temporal_info'])
        elif not n.get('temporal_info'):
            # 如果没有temporal_info，创建一个默认的
            n['temporal_info'] = TemporalInfo()
        
        return StatementNode(**n)
    
    async def find_by_chunk_id(self, chunk_id: str) -> List[StatementNode]:
        """根据chunk_id查询陈述句
        
        Args:
            chunk_id: 分块ID
            
        Returns:
            List[StatementNode]: 陈述句列表
        """
        return await self.find({"chunk_id": chunk_id})
    
    async def find_by_group_id(self, group_id: str, limit: int = 100) -> List[StatementNode]:
        """根据group_id查询陈述句
        
        Args:
            group_id: 组ID
            limit: 返回结果的最大数量
            
        Returns:
            List[StatementNode]: 陈述句列表
        """
        return await self.find({"group_id": group_id}, limit=limit)
    
    async def search_by_embedding(
        self,
        embedding: List[float],
        group_id: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.7
    ) -> List[Dict]:
        """基于向量相似度搜索陈述句
        
        使用余弦相似度计算查询向量与陈述句向量的相似度。
        
        Args:
            embedding: 查询向量
            group_id: 可选的组ID过滤
            limit: 返回结果的最大数量
            min_score: 最小相似度分数阈值
            
        Returns:
            List[Dict]: 包含陈述句和相似度分数的字典列表
                每个字典包含: statement (StatementNode), score (float)
        """
        # 构建查询条件
        where_clause = "n.statement_embedding IS NOT NULL"
        if group_id:
            where_clause += " AND n.group_id = $group_id"
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE {where_clause}
        WITH n, gds.similarity.cosine(n.statement_embedding, $embedding) AS score
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
                "statement": self._map_to_entity(r),
                "score": r.get("score", 0.0)
            }
            for r in results
        ]
    
    async def search_by_keyword(
        self,
        keyword: str,
        group_id: Optional[str] = None,
        limit: int = 50
    ) -> List[StatementNode]:
        """基于关键词搜索陈述句
        
        Args:
            keyword: 搜索关键词
            group_id: 可选的组ID过滤
            limit: 返回结果的最大数量
            
        Returns:
            List[StatementNode]: 陈述句列表
        """
        where_clause = "n.statement CONTAINS $keyword"
        if group_id:
            where_clause += " AND n.group_id = $group_id"
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE {where_clause}
        RETURN n
        LIMIT $limit
        """
        
        params = {"keyword": keyword, "limit": limit}
        if group_id:
            params["group_id"] = group_id
        
        results = await self.connector.execute_query(query, **params)
        return [self._map_to_entity(r) for r in results]
    
    async def find_by_temporal_range(
        self,
        group_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[StatementNode]:
        """根据时间范围查询陈述句
        
        查询在指定时间范围内有效的陈述句。
        
        Args:
            group_id: 组ID
            start_date: 开始日期（可选）
            end_date: 结束日期（可选）
            limit: 返回结果的最大数量
            
        Returns:
            List[StatementNode]: 陈述句列表
        """
        where_clauses = ["n.group_id = $group_id"]
        params = {"group_id": group_id, "limit": limit}
        
        if start_date:
            where_clauses.append("n.valid_at >= $start_date")
            params["start_date"] = start_date.isoformat()
        
        if end_date:
            where_clauses.append("(n.invalid_at IS NULL OR n.invalid_at <= $end_date)")
            params["end_date"] = end_date.isoformat()
        
        where_str = " AND ".join(where_clauses)
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE {where_str}
        RETURN n
        ORDER BY n.created_at DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(query, **params)
        return [self._map_to_entity(r) for r in results]
    
    async def find_strong_statements(
        self,
        group_id: str,
        limit: int = 100
    ) -> List[StatementNode]:
        """查询强连接的陈述句
        
        Args:
            group_id: 组ID
            limit: 返回结果的最大数量
            
        Returns:
            List[StatementNode]: 强连接的陈述句列表
        """
        return await self.find(
            {"group_id": group_id, "connect_strength": "Strong"},
            limit=limit
        )
    
    async def find_by_config_id(
        self,
        config_id: str,
        limit: int = 100
    ) -> List[StatementNode]:
        """根据config_id查询陈述句
        
        Args:
            config_id: 配置ID
            limit: 返回结果的最大数量
            
        Returns:
            List[StatementNode]: 陈述句列表
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
        """基于向量相似度搜索陈述句,可选择按config_id过滤
        
        使用余弦相似度计算查询向量与陈述句向量的相似度。
        支持按config_id过滤结果,确保只返回使用特定配置处理的陈述句。
        
        Args:
            embedding: 查询向量
            config_id: 可选的配置ID过滤
            group_id: 可选的组ID过滤
            limit: 返回结果的最大数量
            min_score: 最小相似度分数阈值
            
        Returns:
            List[Dict]: 包含陈述句和相似度分数的字典列表
                每个字典包含: statement (StatementNode), score (float)
        """
        # 构建查询条件
        where_clauses = ["n.statement_embedding IS NOT NULL"]
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
        WITH n, gds.similarity.cosine(n.statement_embedding, $embedding) AS score
        WHERE score > $min_score
        RETURN n, score
        ORDER BY score DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(query, **params)
        
        return [
            {
                "statement": self._map_to_entity(r),
                "score": r.get("score", 0.0)
            }
            for r in results
        ]
