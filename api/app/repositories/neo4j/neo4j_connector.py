# -*- coding: utf-8 -*-
"""Neo4j连接器模块

本模块提供Neo4j图数据库的连接和查询功能。
从 app/core/memory/src/database/neo4j_connector.py 迁移而来。

Classes:
    Neo4jConnector: Neo4j数据库连接器，提供异步查询接口
"""

from typing import Any, List, Dict

from neo4j import AsyncGraphDatabase, basic_auth

from app.core.config import settings


class Neo4jConnector:
    """Neo4j数据库连接器
    
    提供与Neo4j图数据库的连接和查询功能。
    使用异步驱动程序以支持高并发操作。
    
    Attributes:
        driver: Neo4j异步驱动程序实例
        
    Methods:
        close: 关闭数据库连接
        execute_query: 执行Cypher查询
        delete_group: 删除指定组的所有数据
    """
    
    def __init__(self):
        """初始化Neo4j连接器
        
        从配置文件和环境变量中读取连接信息。
        
        Raises:
            RuntimeError: 如果NEO4J_PASSWORD环境变量未设置
        """
        # 从全局配置和环境变量获取 Neo4j 配置
        uri = settings.NEO4J_URI
        username = settings.NEO4J_USERNAME
        password = settings.NEO4J_PASSWORD
        
        if not password:
            raise RuntimeError(
                "NEO4J_PASSWORD is not set. Create a .env with NEO4J_PASSWORD or export it before running."
            )
        self.driver = AsyncGraphDatabase.driver(
            uri,
            auth=basic_auth(username, password)
        )

    async def close(self):
        """关闭数据库连接
        
        释放数据库连接资源。应在应用程序关闭时调用。
        """
        await self.driver.close()

    async def execute_query(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        """执行Cypher查询
        
        Args:
            query: Cypher查询语句
            **kwargs: 查询参数，将作为参数传递给Cypher查询
            
        Returns:
            List[Dict[str, Any]]: 查询结果列表，每个元素是一个字典
            
        Example:
            >>> connector = Neo4jConnector()
            >>> results = await connector.execute_query(
            ...     "MATCH (n:Person {name: $name}) RETURN n",
            ...     name="Alice"
            ... )
        """
        result = await self.driver.execute_query(
            query,
            database="neo4j",
            **kwargs
        )
        records, summary, keys = result
        return [record.data() for record in records]
    
    async def execute_write_transaction(self, transaction_func, **kwargs: Any) -> Any:
        """在写事务中执行操作
        
        提供显式事务支持，确保操作的原子性。
        如果事务函数抛出异常，所有更改将自动回滚。
        
        Args:
            transaction_func: 事务函数，接收 tx 参数并执行查询
            **kwargs: 传递给事务函数的额外参数
            
        Returns:
            Any: 事务函数的返回值
            
        Example:
            >>> async def create_node(tx, name):
            ...     result = await tx.run(
            ...         "CREATE (n:Person {name: $name}) RETURN n",
            ...         name=name
            ...     )
            ...     return await result.single()
            >>> 
            >>> connector = Neo4jConnector()
            >>> result = await connector.execute_write_transaction(
            ...     create_node, name="Alice"
            ... )
        """
        async with self.driver.session(database="neo4j") as session:
            return await session.execute_write(transaction_func, **kwargs)
    
    async def execute_read_transaction(self, transaction_func, **kwargs: Any) -> Any:
        """在读事务中执行操作
        
        提供显式事务支持用于读操作。
        
        Args:
            transaction_func: 事务函数，接收 tx 参数并执行查询
            **kwargs: 传递给事务函数的额外参数
            
        Returns:
            Any: 事务函数的返回值
            
        Example:
            >>> async def get_node(tx, name):
            ...     result = await tx.run(
            ...         "MATCH (n:Person {name: $name}) RETURN n",
            ...         name=name
            ...     )
            ...     return await result.single()
            >>> 
            >>> connector = Neo4jConnector()
            >>> result = await connector.execute_read_transaction(
            ...     get_node, name="Alice"
            ... )
        """
        async with self.driver.session(database="neo4j") as session:
            return await session.execute_read(transaction_func, **kwargs)
    
    async def delete_group(self, group_id: str):
        """删除指定组的所有数据
        
        删除所有属于指定group_id的节点和边。
        这是一个危险操作，会永久删除数据。
        
        Args:
            group_id: 要删除的组ID
            
        Example:
            >>> connector = Neo4jConnector()
            >>> await connector.delete_group("group_123")
            Group group_123 deleted.
        """
        # 删除节点（DETACH DELETE会同时删除相关的边）
        await self.driver.execute_query(
            "MATCH (n) WHERE n.group_id = $group_id DETACH DELETE n",
            database="neo4j",
            group_id=group_id
        )
        # 删除独立的边（如果有的话）
        await self.driver.execute_query(
            "MATCH ()-[r]->() WHERE r.group_id = $group_id DELETE r",
            database="neo4j",
            group_id=group_id
        )
        print(f"Group {group_id} deleted.")
