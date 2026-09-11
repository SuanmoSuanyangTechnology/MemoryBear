"""Neo4j 连接与查询执行（惰性初始化，由 lifespan 显式控制生命周期）。

与老单体 ``app/repositories/neo4j/neo4j_connector.py`` 的差异：

- **进程级单例 + lifespan 托管**，不用老单体那套 ``shared_driver`` + PID 检测：
  那套是为 Celery（prefork/threads）跨 fork 复用 driver 而设；本服务是单进程
  FastAPI，driver 自身线程安全且带连接池，单例即可（同 infrastructure/redis）。
- **只读路由**：查询一律 ``routing_=READ``。本服务对图只读（写入归 memory-jobs），
  走读路由才不会把读打到写主库；集群下也避免误路由。
- **驱动 6.x 显式参数**：``parameters_`` / ``database_``。老单体用的
  ``execute_query(cypher, database="neo4j", **kwargs)``（kwargs 当查询参数）在 6.x
  仍可用但已非推荐形式，本服务用显式签名。
- **返回值恒为 JSON 安全类型**：neo4j 的 DateTime/Date/Time/Duration 在驱动层是
  独立类型（``neo4j.time``），直接进 FastAPI 响应会序列化失败；此处统一转成
  ISO 字符串/原生类型（老单体是 ``json_format=True`` 时才转，属可选）。
"""

from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, RoutingControl, basic_auth
from neo4j.time import Date as Neo4jDate
from neo4j.time import DateTime as Neo4jDateTime
from neo4j.time import Duration as Neo4jDuration
from neo4j.time import Time as Neo4jTime

from src.config import settings
from src.infrastructure.logger.config import get_logger
from src.utils.datetime_utils import to_iso_z

logger = get_logger(__name__)

# 图数据库名（与老单体一致，固定 "neo4j"）
DATABASE = "neo4j"

_driver: AsyncDriver | None = None


def _jsonify(value: Any) -> Any:
    """递归把 neo4j 原生时间类型转成可 JSON 序列化的值。

    DateTime 有 tzinfo 时转 UTC ISO（带 Z）；无时区时用驱动自带 ISO 格式
    （不能硬塞 UTC，否则把本地时间谎报成 UTC）。
    """
    if isinstance(value, Neo4jDateTime):
        return to_iso_z(value.to_native()) if value.tzinfo else value.iso_format()
    if isinstance(value, (Neo4jDate, Neo4jTime)):
        return value.iso_format()
    if isinstance(value, Neo4jDuration):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    return value


async def get_neo4j_driver() -> AsyncDriver:
    """取进程级 driver 单例（惰性建连）。"""
    global _driver
    if _driver is None:
        if not settings.NEO4J_PASSWORD:
            raise RuntimeError(
                "NEO4J_PASSWORD 未配置。请在 .env 中设置，或通过环境变量导出后再启动。"
            )
        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=basic_auth(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
            max_connection_pool_size=settings.NEO4J_MAX_POOL_SIZE,
            connection_acquisition_timeout=settings.NEO4J_ACQ_TIMEOUT,
            max_connection_lifetime=settings.NEO4J_MAX_CONN_LIFETIME,
            connection_timeout=settings.NEO4J_CONN_TIMEOUT,
        )
        logger.info("Neo4j driver created (uri=%s)", settings.NEO4J_URI)
    return _driver


async def init_neo4j() -> None:
    """启动期探活：连不上即启动失败（fail-fast，避免首次请求才报错）。"""
    await (await get_neo4j_driver()).verify_connectivity()


async def close_neo4j() -> None:
    """关闭 driver 并释放连接池（lifespan 退出时调用）。"""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


async def execute_read_query(cypher: str, /, **params: Any) -> list[dict[str, Any]]:
    """执行只读 Cypher，返回 ``[{列名: 值}]``（值已转 JSON 安全类型）。

    Args:
        cypher: Cypher 语句，参数用 ``$name`` 占位（**不要字符串拼接**——动态
            部分（如节点 label）应通过 ``repositories/cypher`` 里的 builder 做白名单
            校验后内联，见该模块说明）。
        **params: 查询参数。

    Returns:
        记录列表；每条记录是 ``{列名: 值}``。无结果返回空列表。
    """
    driver = await get_neo4j_driver()
    result = await driver.execute_query(
        cypher,
        parameters_=params,
        database_=DATABASE,
        routing_=RoutingControl.READ,
    )
    records, _summary, _keys = result
    return [
        {k: _jsonify(v) for k, v in record.data().items()}
        for record in records
    ]


__all__ = [
    "DATABASE",
    "close_neo4j",
    "execute_read_query",
    "get_neo4j_driver",
    "init_neo4j",
]