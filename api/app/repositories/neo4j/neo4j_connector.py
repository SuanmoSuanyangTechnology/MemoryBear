# -*- coding: utf-8 -*-
"""Neo4j连接器模块

改造为全局 driver 引用持有者（Phase 1）。

改造要点：
  - __init__ 不再创建 driver，通过 driver_provider.get_driver_sync() 获取
  - close() 不关闭进程级 driver（兼容旧调用点的 try/finally）
  - 支持外部注入 driver（测试用）
  - 所有现有调用点零改动即可兼容

Classes:
    Neo4jConnector: Neo4j数据库连接器，提供异步查询接口
"""

from typing import Any, List, Dict, Optional

from neo4j import AsyncDriver
from neo4j.time import (
    DateTime as Neo4jDateTime,
    Date as Neo4jDate,
    Time as Neo4jTime,
    Duration as Neo4jDuration,
)

from app.core.config import settings


def _convert_neo4j_types(value: Any) -> Any:
    """递归将 neo4j 原生时间类型转为 Python 原生类型 / ISO 字符串，确保可被 json.dumps 序列化。"""
    if isinstance(value, Neo4jDateTime):
        return value.to_native().isoformat() if value.tzinfo else value.iso_format()
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

    持有全局 driver 引用的薄壳。close() 不再关闭进程级 driver，
    仅当外部注入了独立 driver 时才真正关闭。

    Attributes:
        driver: Neo4j异步驱动程序实例（property，lazy 获取）

    Methods:
        close: 兼容旧调用（通常为 no-op）
        execute_query: 执行Cypher查询
        execute_write_transaction: 在写事务中执行操作
        execute_read_transaction: 在读事务中执行操作
        delete_group: 删除指定组的所有数据
    """

    def __init__(self, driver: Optional[AsyncDriver] = None):
        """初始化Neo4j连接器。

        Args:
            driver: 可选的外部注入 driver（测试用）。
                    为 None 时从 driver_provider 获取全局单例。
        """
        self._external_driver = driver
        self._driver: Optional[AsyncDriver] = driver

    @property
    def driver(self) -> AsyncDriver:
        """获取 driver 实例。首次访问时从 driver_provider 获取。"""
        if self._driver is None:
            from app.repositories.neo4j.driver_provider import get_driver_sync
            self._driver = get_driver_sync()
        return self._driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """关闭数据库连接（兼容旧调用）。

        仅当外部传入了独立 driver 时才真正关闭。
        对于使用全局 driver 的场景，此方法为 no-op。
        """
        if self._external_driver is not None:
            await self._external_driver.close()
        self._driver = None

    async def execute_query(self, cypher: str, json_format=False, **kwargs: Any) -> List[Dict[str, Any]]:
        """执行Cypher查询

        Args:
            cypher: Cypher查询语句
            json_format: json格式化
            **kwargs: 查询参数，将作为参数传递给Cypher查询

        Returns:
            List[Dict[str, Any]]: 查询结果列表，每个元素是一个字典
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
        """
        async with self.driver.session(database="neo4j") as session:
            return await session.execute_read(transaction_func, **kwargs)

    async def delete_group(self, end_user_id: str):
        """删除指定组的所有数据

        删除所有属于指定end_user_id的节点和边。
        这是一个危险操作，会永久删除数据。

        Args:
            end_user_id: 要删除的组ID
        """
        # 删除节点（DETACH DELETE会同时删除相关的边）
        await self.driver.execute_query(
            "MATCH (n) WHERE n.end_user_id = $end_user_id DETACH DELETE n",
            database="neo4j",
            end_user_id=end_user_id
        )
        # 删除独立的边（如果有的话）
        await self.driver.execute_query(
            "MATCH ()-[r]->() WHERE r.end_user_id = $end_user_id DELETE r",
            database="neo4j",
            end_user_id=end_user_id
        )
        print(f"Group {end_user_id} deleted.")
