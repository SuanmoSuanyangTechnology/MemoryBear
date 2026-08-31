"""Outbox projection clients and source-of-truth checks."""

import asyncio
from collections.abc import Mapping

from elasticsearch import AsyncElasticsearch
from neo4j import AsyncGraphDatabase
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app.core.config import settings
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import NodeFilter, StorageReadResult
from app.core.memory.storage.outbox.types import ClaimedEvent, OutboxOperation
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.elasticsearch.config import (
    build_elasticsearch_client_config,
)
from app.core.memory.storage.provider.elasticsearch.index import ensure_indices
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage.provider.neo4j.config import build_neo4j_driver_config


async def project_event(
    event: ClaimedEvent,
    neo4j: Neo4jClient,
    elastic: ElasticClient,
    *,
    check_claim,
) -> None:
    label = MemoryNodeType(event.label)
    operation = OutboxOperation(event.operation)
    node_filter = NodeFilter.eq("id", event.node_id)

    if operation is OutboxOperation.DELETE:
        # 物理删除事件不回读 Neo4j：节点已不存在，直接删除 ES 文档。
        await check_claim()
        await elastic.delete_node(label, node_filter, draft=False)
        return

    # upsert 与 draft_delete 都以 Neo4j 当前态为准；软删除节点仍然存在，
    # 回读后会把 delete_at 一并覆盖写入 ES。
    result = await neo4j.get_node(label=label, node_filter=node_filter)
    if (
        not isinstance(result, StorageReadResult)
        or result.total != len(result.items)
        or result.total > 1
    ):
        raise ValueError("Invalid or ambiguous authoritative node result")
    document = None
    if result.items:
        candidate = result.items[0]
        data = candidate.data if candidate is not None else None
        if (
            not isinstance(data, Mapping)
            or data.get("id") != event.node_id
        ):
            raise ValueError("Authoritative node identity mismatch")
        document = data
    # 读错误绝不转化为 ES 删除。慢读后需重新校验租约归属。
    await check_claim()
    if document is not None:
        await elastic.save_node(label, document)
    else:
        # 节点已被物理删除，投影收敛为删除 ES 文档。
        await elastic.delete_node(label, node_filter, draft=False)


class ProjectionClients:
    """按批次惰性初始化一次，并在同一事件循环中关闭。"""

    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout
        self.neo4j = None
        self.elastic = None
        self.redis = None
        self.indices_ready = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        # 即使其中一个关闭失败也要关闭另一个；绝不记录原始 SDK 异常。
        closing = [
            client.close()
            for client in (self.neo4j, self.elastic)
            if client is not None
        ]
        if self.redis is not None:
            closing.append(self.redis.aclose())
        await asyncio.gather(*closing, return_exceptions=True)

    async def project(self, event, check_claim):
        if self.neo4j is None:
            config = build_neo4j_driver_config()
            config.update(
                max_transaction_retry_time=0,
                connection_timeout=min(10, self.request_timeout),
                connection_acquisition_timeout=min(10, self.request_timeout),
            )
            client = Neo4jClient()
            client.client = AsyncGraphDatabase.driver(**config)
            self.neo4j = client
        if self.elastic is None:
            config = build_elasticsearch_client_config()
            config.update(
                request_timeout=self.request_timeout,
                max_retries=0,
                retry_on_timeout=False,
            )
            client = ElasticClient()
            client.client = AsyncElasticsearch(**config)
            self.elastic = client
        if not self.indices_ready:
            if self.redis is None:
                # 同时接管 migration-lock 的连接池：不要让 socket 留在
                # 即将被 Celery asyncio.run 关闭的事件循环上。
                self.redis = Redis(
                    host=settings.REDIS_HOST,
                    port=settings.REDIS_PORT,
                    db=settings.REDIS_DB,
                    password=settings.REDIS_PASSWORD,
                    decode_responses=True,
                    socket_connect_timeout=min(10, self.request_timeout),
                    socket_timeout=self.request_timeout,
                    retry_on_timeout=False,
                    retry=Retry(NoBackoff(), 0),
                )
            await ensure_indices(
                self.elastic.client,
                redis_client=self.redis,
            )
            self.indices_ready = True
        await project_event(
            event,
            self.neo4j,
            self.elastic,
            check_claim=check_claim,
        )
