# -*- coding: utf-8 -*-
"""陈述句仓储模块

本模块提供陈述句节点的数据访问功能。

Classes:
    StatementRepository: 陈述句仓储，管理StatementNode的CRUD操作
"""

from typing import List, Dict
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
        
        return StatementNode(**n)
    
    async def find_by_chunk_id(self, chunk_id: str) -> List[StatementNode]:
        """根据chunk_id查询陈述句
        
        Args:
            chunk_id: 分块ID
            
        Returns:
            List[StatementNode]: 陈述句列表
        """
        return await self.find({"chunk_id": chunk_id})
