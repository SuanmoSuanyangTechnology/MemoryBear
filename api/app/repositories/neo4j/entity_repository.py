"""实体仓储模块

本模块提供实体节点的数据访问功能。

Classes:
    EntityRepository: 实体仓储，管理ExtractedEntityNode的CRUD操作
"""

from typing import List, Dict
from datetime import datetime

from app.repositories.neo4j.base_neo4j_repository import BaseNeo4jRepository
from app.core.memory.models.graph_models import ExtractedEntityNode
from app.core.memory.utils.data.ontology import get_type_id
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
        
        # 确保aliases字段存在且为列表
        if 'aliases' not in n or n['aliases'] is None:
            n['aliases'] = []
        
        # 处理 ACT-R 属性 - 确保字段存在且有默认值
        n['importance_score'] = n.get('importance_score', 0.5)
        n['activation_value'] = n.get('activation_value')
        n['access_history'] = n.get('access_history') or []
        n['last_access_time'] = n.get('last_access_time')
        n['access_count'] = n.get('access_count', 0)
        
        # 确保 type_id 字段存在（历史数据可能没有此属性或为 0，根据 entity_type 反查）
        if not n.get('type_id'):
            n['type_id'] = get_type_id(n.get('entity_type', ''))
        
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
    