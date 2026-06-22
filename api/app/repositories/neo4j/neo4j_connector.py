# -*- coding: utf-8 -*-
"""Neo4j连接器模块

本模块提供Neo4j图数据库的连接和查询功能。
从 app/core/memory/src/database/neo4j_connector.py 迁移而来。

Classes:
    Neo4jConnector: Neo4j数据库连接器，提供异步查询接口
"""

import logging
import os
import threading
from typing import Any, List, Dict

from neo4j import AsyncDriver, AsyncGraphDatabase, basic_auth
from neo4j.time import DateTime as Neo4jDateTime, Date as Neo4jDate, Time as Neo4jTime, Duration as Neo4jDuration

from app.core.config import settings
from app.core.utils.datetime_utils import to_iso_z

logger = logging.getLogger(__name__)


def _convert_neo4j_types(value: Any) -> Any:
    """递归将 neo4j 原生时间类型转为 Python 原生类型 / ISO 字符串，确保可被 json.dumps 序列化。"""
    if isinstance(value, Neo4jDateTime):
        return to_iso_z(value.to_native()) if value.tzinfo else value.iso_format()
    if isinstance(value, Neo4jDate):
        return value.iso_format()
    if isinstance(value, Neo4jTime):
        return value.iso_format()
    if isinstance(value, Neo4jDuration):
        return str(value)
    if isinstance(value, dict):
        return {k: _convert_neo4j_types(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_convert_neo4j_types(item) for item in value]
    return value


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
    
    _shared_driver: AsyncDriver | None = None
    _shared_driver_pid: int | None = None
    _shared_driver_lock = threading.Lock()

    def __init__(self, shared_driver: bool = False):
        """初始化Neo4j连接器
        
        从配置文件和环境变量中读取连接信息。
        
        Raises:
            RuntimeError: 如果NEO4J_PASSWORD环境变量未设置
        """
        self._shared_driver_enabled = shared_driver
        self.driver = self._create_or_get_driver()

    @classmethod
    def _build_driver(cls) -> AsyncDriver:
        """创建新的 Neo4j driver。"""
        uri = settings.NEO4J_URI
        username = settings.NEO4J_USERNAME
        password = settings.NEO4J_PASSWORD

        if not password:
            raise RuntimeError(
                "NEO4J_PASSWORD is not set. Create a .env with NEO4J_PASSWORD or export it before running."
            )
        return AsyncGraphDatabase.driver(
            uri,
            auth=basic_auth(username, password)
        )

    def _create_or_get_driver(self) -> AsyncDriver:
        """按配置返回独占或进程级共享的 driver。"""
        if not self._shared_driver_enabled:
            return self._build_driver()

        current_pid = os.getpid()
        driver = self.__class__._shared_driver
        if driver is not None and self.__class__._shared_driver_pid == current_pid:
            return driver

        with self.__class__._shared_driver_lock:
            driver = self.__class__._shared_driver
            if driver is None or self.__class__._shared_driver_pid != current_pid:
                driver = self._build_driver()
                self.__class__._shared_driver = driver
                self.__class__._shared_driver_pid = current_pid
            return driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """关闭数据库连接

        释放数据库连接资源。应在应用程序关闭时调用。
        """
        if self._shared_driver_enabled:
            return
        await self.driver.close()

    @classmethod
    async def shutdown(cls):
        """关闭进程级共享 driver，由应用 shutdown 生命周期调用。"""
        driver = cls._shared_driver
        if driver is not None:
            cls._shared_driver = None
            cls._shared_driver_pid = None
            await driver.close()

    async def execute_query(self, cypher: str, json_format=False, **kwargs: Any) -> List[Dict[str, Any]]:
        """执行Cypher查询
        
        Args:
            cypher: Cypher查询语句
            json_format: json格式化
            **kwargs: 查询参数，将作为参数传递给Cypher查询
            
        Returns:
            List[Dict[str, Any]]: 查询结果列表，每个元素是一个字典
            
        Example:

        """
        result = await self.driver.execute_query(
            cypher,
            database="neo4j",
            **kwargs
        )
        records, summary, keys = result
        if json_format:
            return [_convert_neo4j_types(record.data()) for record in records]
        else:
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

        """
        async with self.driver.session(database="neo4j") as session:
            return await session.execute_read(transaction_func, **kwargs)
    
    async def delete_group(self, end_user_id: str):
        """删除指定组的所有数据
        
        删除所有属于指定end_user_id的节点和边。
        这是一个危险操作，会永久删除数据。
        
        Args:
            end_user_id: 要删除的组ID
            
        Example:
            Group group_123 deleted.
        """
        batch_size = 1000
        total_deleted = 0
        while True:
            records = await self.execute_query(
                "MATCH (n) WHERE n.end_user_id = $end_user_id "
                "WITH n LIMIT $batch_size "
                "DETACH DELETE n "
                "RETURN count(n) AS deleted",
                end_user_id=end_user_id,
                batch_size=batch_size,
            )
            count = records[0]["deleted"] if records else 0
            if count == 0:
                break
            total_deleted += count

        await self.execute_query(
            "MATCH ()-[r]->() WHERE r.end_user_id = $end_user_id DELETE r",
            end_user_id=end_user_id,
        )
        logger.info(f"Group {end_user_id} deleted ({total_deleted} nodes).")
