# -*- coding: utf-8 -*-
"""陈述句仓储模块

本模块提供陈述句节点的数据访问功能。

Classes:
    StatementRepository: 陈述句仓储，管理StatementNode的CRUD操作
"""

from typing import Any, Dict, List, Sequence
from datetime import datetime

from app.repositories.neo4j.base_neo4j_repository import BaseNeo4jRepository
from app.core.memory.models.graph_models import StatementNode
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.utils.data.ontology import TemporalInfo
from app.repositories.neo4j.cypher_queries import (
    INTEREST_ENTITY_CANDIDATES_BY_END_USER,
    INTEREST_ENTITY_CANDIDATES_BY_USER,
    INTEREST_STATEMENT_EVIDENCE_BY_END_USER,
    INTEREST_STATEMENT_EVIDENCE_BY_USER,
)


class StatementRepository(BaseNeo4jRepository[StatementNode]):
    """陈述句仓储
    
    管理陈述句节点的创建、查询、更新和删除操作。
    提供按chunk_id、end_user_id、向量相似度等条件查询陈述句的方法。
    
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
        if n.get('valid_at') and isinstance(n['valid_at'], str):
            n['valid_at'] = datetime.fromisoformat(n['valid_at'])
        if n.get('invalid_at') and isinstance(n['invalid_at'], str):
            n['invalid_at'] = datetime.fromisoformat(n['invalid_at'])
        if n.get('dialog_at') and isinstance(n['dialog_at'], str):
            n['dialog_at'] = datetime.fromisoformat(n['dialog_at'])
        
        # 处理temporal_info字段
        if isinstance(n.get('temporal_info'), str):
            # 从字符串转换为枚举值
            n['temporal_info'] = TemporalInfo(n['temporal_info'])
        elif isinstance(n.get('temporal_info'), dict):
            n['temporal_info'] = TemporalInfo(**n['temporal_info'])
        elif not n.get('temporal_info'):
            # 如果没有temporal_info，创建一个默认的
            n['temporal_info'] = TemporalInfo.STATIC
        
        # 处理情绪字段 - 映射 Neo4j 节点属性到 StatementNode 模型
        # 处理空值情况，确保字段存在
        n['emotion_type'] = n.get('emotion_type')
        n['emotion_intensity'] = n.get('emotion_intensity')
        n['emotion_keywords'] = n.get('emotion_keywords', [])
        n['emotion_subject'] = n.get('emotion_subject')
        n['emotion_target'] = n.get('emotion_target')
        
        # 处理 ACT-R 属性 - 确保字段存在且有默认值
        n['importance_score'] = n.get('importance_score', 0.5)
        n['activation_value'] = n.get('activation_value')
        n['access_history'] = n.get('access_history') or []
        n['last_access_time'] = n.get('last_access_time')
        n['access_count'] = n.get('access_count', 0)
        
        return StatementNode(**n)
    
    async def find_by_chunk_id(self, chunk_id: str) -> List[StatementNode]:
        """根据chunk_id查询陈述句
        
        Args:
            chunk_id: 分块ID
            
        Returns:
            List[StatementNode]: 陈述句列表
        """
        return await self.find({"chunk_id": chunk_id})

    async def find_recent_valid_user_statements(
        self,
        end_user_id: str,
        statement_types: Sequence[str],
        limit: int,
    ) -> List[str]:
        """查询用户最近的有效 Statement 正文。"""
        query = """
        MATCH (s:Statement)
        WHERE s.end_user_id = $end_user_id
          AND s.delete_at IS NULL
          AND s.speaker = "user"
          AND coalesce(s.has_unsolved_reference, false) = false
          AND s.stmt_type IN $statement_types
          AND s.statement IS NOT NULL
          AND trim(s.statement) <> ""
        RETURN s.statement AS statement
        ORDER BY s.dialog_at DESC, s.id ASC
        LIMIT $limit
        """
        results = await self.connector.execute_query(
            query,
            end_user_id=end_user_id,
            statement_types=list(statement_types),
            limit=limit,
        )
        return [record["statement"] for record in results]

    async def find_interest_entity_candidates(
        self,
        user_id: str,
        limit: int,
        excluded_names: Sequence[str],
        iso_datetime_pattern: str,
        unix_timestamp_pattern: str,
        by_user: bool = False,
    ) -> List[Dict[str, Any]]:
        """按有效 Statement 数量查询兴趣候选实体。"""
        query = (
            INTEREST_ENTITY_CANDIDATES_BY_USER
            if by_user
            else INTEREST_ENTITY_CANDIDATES_BY_END_USER
        )
        return await self.connector.execute_query(
            query,
            id=user_id,
            limit=limit,
            excluded_names=list(excluded_names),
            iso_datetime_pattern=iso_datetime_pattern,
            unix_timestamp_pattern=unix_timestamp_pattern,
        )

    async def find_interest_statement_evidence(
        self,
        user_id: str,
        entity_ids: Sequence[str],
        per_entity_limit: int,
        limit: int,
        max_chars: int,
        by_user: bool = False,
    ) -> List[Dict[str, Any]]:
        """批量查询兴趣候选实体关联的有效 Statement 证据。"""
        if not entity_ids:
            return []

        query = (
            INTEREST_STATEMENT_EVIDENCE_BY_USER
            if by_user
            else INTEREST_STATEMENT_EVIDENCE_BY_END_USER
        )
        return await self.connector.execute_query(
            query,
            id=user_id,
            entity_ids=list(entity_ids),
            per_entity_limit=per_entity_limit,
            limit=limit,
            max_chars=max_chars,
        )
