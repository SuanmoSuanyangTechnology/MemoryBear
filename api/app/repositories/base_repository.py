"""基础仓储接口模块

本模块定义了通用的仓储接口，适用于所有数据库类型（PostgreSQL、Neo4j等）。
遵循仓储模式（Repository Pattern），提供统一的数据访问抽象。

Classes:
    BaseRepository: 基础仓储接口，定义CRUD操作的抽象方法
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, List, Optional, Dict, Any

T = TypeVar('T')


class BaseRepository[T](ABC):
    """基础仓储接口 - 适用于所有数据库类型
    
    这是一个抽象基类，定义了所有仓储必须实现的基本CRUD操作。
    使用泛型T来支持不同的实体类型。
    
    Type Parameters:
        T: 实体类型，通常是Pydantic模型或ORM模型
    
    Methods:
        create: 创建新实体
        get_by_id: 根据ID获取实体
        update: 更新现有实体
        delete: 删除实体
        find: 根据条件查询实体列表
    """
    
    @abstractmethod
    async def create(self, entity: T) -> T:
        """创建实体
        
        Args:
            entity: 要创建的实体对象
            
        Returns:
            T: 创建后的实体对象（可能包含生成的ID等）
            
        Raises:
            Exception: 创建失败时抛出异常
        """
        pass
    
    @abstractmethod
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        """根据ID获取实体
        
        Args:
            entity_id: 实体的唯一标识符
            
        Returns:
            Optional[T]: 找到的实体对象，如果不存在则返回None
            
        Raises:
            Exception: 查询失败时抛出异常
        """
        pass
    
    @abstractmethod
    async def update(self, entity: T) -> T:
        """更新实体
        
        Args:
            entity: 要更新的实体对象（必须包含ID）
            
        Returns:
            T: 更新后的实体对象
            
        Raises:
            Exception: 更新失败时抛出异常
        """
        pass
    
    @abstractmethod
    async def delete(self, entity_id: str) -> bool:
        """删除实体
        
        Args:
            entity_id: 要删除的实体ID
            
        Returns:
            bool: 删除成功返回True，否则返回False
            
        Raises:
            Exception: 删除失败时抛出异常
        """
        pass
    
    @abstractmethod
    async def find(self, filters: Dict[str, Any], limit: int = 100) -> List[T]:
        """查询实体列表
        
        Args:
            filters: 查询条件字典，键为字段名，值为期望的值
            limit: 返回结果的最大数量，默认100
            
        Returns:
            List[T]: 符合条件的实体列表
            
        Raises:
            Exception: 查询失败时抛出异常
        """
        pass
