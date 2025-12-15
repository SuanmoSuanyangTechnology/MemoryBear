"""Neo4j仓储基类模块

本模块提供Neo4j仓储的基类实现，封装了通用的Neo4j节点操作。

Classes:
    BaseNeo4jRepository: Neo4j仓储基类，实现通用的CRUD操作
"""

from typing import List, Optional, Dict, Any, TypeVar
from app.repositories.base_repository import BaseRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

T = TypeVar('T')


class BaseNeo4jRepository(BaseRepository[T]):
    """Neo4j仓储基类 - 实现通用的Neo4j节点操作
    
    这个基类封装了Neo4j节点的通用CRUD操作，子类只需要实现
    特定的映射逻辑和业务查询方法。
    
    Attributes:
        connector: Neo4j连接器实例
        node_label: 节点标签（如"Dialogue", "Statement"等）
        
    Type Parameters:
        T: 实体类型，通常是Pydantic模型
    """
    
    def __init__(self, connector: Neo4jConnector, node_label: str):
        """初始化Neo4j仓储
        
        Args:
            connector: Neo4j连接器实例
            node_label: 节点标签，用于Cypher查询
        """
        self.connector = connector
        self.node_label = node_label
    
    async def create(self, entity: T) -> T:
        """创建节点
        
        将实体对象转换为Neo4j节点并保存到数据库。
        
        Args:
            entity: 要创建的实体对象
            
        Returns:
            T: 创建后的实体对象
            
        Example:
            >>> dialog = DialogueNode(id="123", name="对话1", ...)
            >>> created = await repository.create(dialog)
        """
        query = f"""
        CREATE (n:{self.node_label} $props)
        RETURN n
        """
        # 使用model_dump()获取所有字段，包括aliases
        props = entity.model_dump()
        
        # 确保aliases字段存在且为列表（针对ExtractedEntity节点）
        if hasattr(entity, 'aliases'):
            if props.get('aliases') is None:
                props['aliases'] = []
        
        result = await self.connector.execute_query(
            query,
            props=props
        )
        return entity
    
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        """根据ID获取节点
        
        Args:
            entity_id: 节点ID
            
        Returns:
            Optional[T]: 找到的实体对象，如果不存在则返回None
        """
        query = f"""
        MATCH (n:{self.node_label} {{id: $id}})
        RETURN n
        """
        result = await self.connector.execute_query(query, id=entity_id)
        if result:
            return self._map_to_entity(result[0])
        return None
    
    async def update(self, entity: T) -> T:
        """更新节点
        
        更新现有节点的属性。使用SET +=语法合并属性。
        
        Args:
            entity: 要更新的实体对象（必须包含id字段）
            
        Returns:
            T: 更新后的实体对象
        """
        query = f"""
        MATCH (n:{self.node_label} {{id: $id}})
        SET n += $props
        RETURN n
        """
        # 使用model_dump()获取所有字段，包括aliases
        props = entity.model_dump()
        
        # 确保aliases字段存在且为列表（针对ExtractedEntity节点）
        if hasattr(entity, 'aliases'):
            if props.get('aliases') is None:
                props['aliases'] = []
        
        await self.connector.execute_query(
            query,
            id=entity.id,
            props=props
        )
        return entity
    
    async def delete(self, entity_id: str) -> bool:
        """删除节点
        
        删除指定ID的节点。使用DETACH DELETE同时删除相关的边。
        
        Args:
            entity_id: 要删除的节点ID
            
        Returns:
            bool: 删除成功返回True，否则返回False
        """
        query = f"""
        MATCH (n:{self.node_label} {{id: $id}})
        DETACH DELETE n
        RETURN count(n) as deleted
        """
        result = await self.connector.execute_query(query, id=entity_id)
        return result[0]['deleted'] > 0 if result else False
    
    async def find(self, filters: Dict[str, Any], limit: int = 100) -> List[T]:
        """查询节点
        
        根据过滤条件查询节点列表。
        
        Args:
            filters: 查询条件字典，键为属性名，值为期望的值
            limit: 返回结果的最大数量
            
        Returns:
            List[T]: 符合条件的实体列表
            
        Example:
            >>> results = await repository.find(
            ...     {"group_id": "group_123", "user_id": "user_456"},
            ...     limit=50
            ... )
        """
        # 构建查询条件
        where_clauses = [f"n.{key} = ${key}" for key in filters]
        where_str = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE {where_str}
        RETURN n
        LIMIT $limit
        """
        results = await self.connector.execute_query(
            query,
            limit=limit,
            **filters
        )
        return [self._map_to_entity(r) for r in results]
    
    def _map_to_entity(self, node_data: Dict) -> T:
        """将节点数据映射为实体对象
        
        这是一个抽象方法，子类必须实现具体的映射逻辑。
        
        Args:
            node_data: 从Neo4j查询返回的节点数据字典
            
        Returns:
            T: 映射后的实体对象
            
        Raises:
            NotImplementedError: 如果子类未实现此方法
        """
        raise NotImplementedError("Subclasses must implement _map_to_entity method")
