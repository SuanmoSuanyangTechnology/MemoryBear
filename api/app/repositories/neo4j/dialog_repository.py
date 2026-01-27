# -*- coding: utf-8 -*-
"""对话仓储模块

本模块提供对话节点的数据访问功能。

Classes:
    DialogRepository: 对话仓储，管理DialogueNode的CRUD操作
"""

from typing import List, Optional, Dict
from datetime import datetime

from app.repositories.neo4j.base_neo4j_repository import BaseNeo4jRepository
from app.core.memory.models.graph_models import DialogueNode
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


class DialogRepository(BaseNeo4jRepository[DialogueNode]):
    """对话仓储
    
    管理对话节点的创建、查询、更新和删除操作。
    提供按end_user_id、user_id、ref_id等条件查询对话的方法。
    
    Attributes:
        connector: Neo4j连接器实例
        node_label: 节点标签，固定为"Dialogue"
    """
    
    def __init__(self, connector: Neo4jConnector):
        """初始化对话仓储
        
        Args:
            connector: Neo4j连接器实例
        """
        super().__init__(connector, "Dialogue")
    
    def _map_to_entity(self, node_data: Dict) -> DialogueNode:
        """将节点数据映射为对话实体
        
        Args:
            node_data: 从Neo4j查询返回的节点数据字典
            
        Returns:
            DialogueNode: 对话实体对象
        """
        # 从查询结果中提取节点数据
        n = node_data.get('n', node_data)
        
        # 处理datetime字段
        if isinstance(n.get('created_at'), str):
            n['created_at'] = datetime.fromisoformat(n['created_at'])
        if n.get('expired_at') and isinstance(n['expired_at'], str):
            n['expired_at'] = datetime.fromisoformat(n['expired_at'])
        
        return DialogueNode(**n)
    
    async def find_by_end_user_id(self, end_user_id: str, limit: int = 100) -> List[DialogueNode]:
        """根据end_user_id查询对话
        
        Args:
            end_user_id: 组ID
            limit: 返回结果的最大数量
            
        Returns:
            List[DialogueNode]: 对话列表
        """
        return await self.find({"end_user_id": end_user_id}, limit=limit)
    
    async def find_by_user_id(self, user_id: str, limit: int = 100) -> List[DialogueNode]:
        """根据user_id查询对话
        
        Args:
            user_id: 用户ID
            limit: 返回结果的最大数量
            
        Returns:
            List[DialogueNode]: 对话列表
        """
        return await self.find({"user_id": user_id}, limit=limit)
    
    async def find_by_ref_id(self, ref_id: str) -> Optional[DialogueNode]:
        """根据ref_id查询对话
        
        ref_id是外部对话系统的引用ID，通常是唯一的。
        
        Args:
            ref_id: 引用ID
            
        Returns:
            Optional[DialogueNode]: 找到的对话，如果不存在则返回None
        """
        results = await self.find({"ref_id": ref_id}, limit=1)
        return results[0] if results else None
    
    async def find_by_group_and_user(
        self,
        end_user_id: str,
        user_id: str,
        limit: int = 100
    ) -> List[DialogueNode]:
        """根据end_user_id和user_id查询对话
        
        Args:
            end_user_id: 组ID
            user_id: 用户ID
            limit: 返回结果的最大数量
            
        Returns:
            List[DialogueNode]: 对话列表
        """
        return await self.find(
            {"end_user_id": end_user_id, "user_id": user_id},
            limit=limit
        )
    
    async def find_recent_dialogs(
        self,
        end_user_id: str,
        days: int = 7,
        limit: int = 100
    ) -> List[DialogueNode]:
        """查询最近的对话
        
        Args:
            end_user_id: 组ID
            days: 查询最近多少天的对话
            limit: 返回结果的最大数量
            
        Returns:
            List[DialogueNode]: 对话列表，按创建时间倒序排列
        """
        query = f"""
        MATCH (n:{self.node_label})
        WHERE n.end_user_id = $end_user_id
        AND n.created_at >= datetime() - duration({{days: $days}})
        RETURN n
        ORDER BY n.created_at DESC
        LIMIT $limit
        """
        results = await self.connector.execute_query(
            query,
            end_user_id=end_user_id,
            days=days,
            limit=limit
        )
        return [self._map_to_entity(r) for r in results]
    
    async def find_by_config_id(
        self,
        config_id: str,
        limit: int = 100
    ) -> List[DialogueNode]:
        """根据config_id查询对话
        
        Args:
            config_id: 配置ID
            limit: 返回结果的最大数量
            
        Returns:
            List[DialogueNode]: 对话列表
        """
        return await self.find({"config_id": config_id}, limit=limit)
    
    async def find_by_config_and_group(
        self,
        config_id: str,
        end_user_id: str,
        limit: int = 100
    ) -> List[DialogueNode]:
        """根据config_id和end_user_id查询对话
        
        支持按配置ID和组ID同时过滤,确保只返回使用特定配置处理的对话。
        
        Args:
            config_id: 配置ID
            end_user_id: 组ID
            limit: 返回结果的最大数量
            
        Returns:
            List[DialogueNode]: 对话列表
        """
        return await self.find(
            {"config_id": config_id, "end_user_id": end_user_id},
            limit=limit
        )
