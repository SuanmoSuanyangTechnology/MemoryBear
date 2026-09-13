"""只读 SQL registry 基座（Sync/Async 双态）。

SQL 骨架内置在本模块（最朴素的 select + tenant/provider/is_active 过滤），宿主只注入
ORM 映射类与行投影（RegistrySQLSource）——列一律从注入 mapper 的属性取，无列名
硬编码、也不在包内另建 Table 映射。model_names 覆盖命中、能力过滤、候选排序属
resolver（M2 Task 8），本模块只提供「config 快照 + 租户活跃渠道快照池 + TTL 缓存」。

- 快照缓存（spec §11.1）：per (tenant_id, provider) 全量活跃渠道，TTL 默认 60s；
  宿主写路径变更后调 invalidate() 主动失效（同租户全量键一并失效，避免读到旧全集）。
  缓存实例（ChannelSnapshotCache）可经构造器 cache 参数在多个 registry 间共享——
  宿主持进程级实例 + per-request registry 复用，TTL 才真正生效。
- 无任何宿主 import；凭据解密/密钥不进本模块（ChannelSnapshot 携带密文信封原样，
  解密收敛在 resolver 取凭据处）。provider=composite 守卫在登记层，不在本模块。
- 服务化终局：model-service 注册自身 ORM 映射即用，读语义零搬迁；core/api 与
  mem-knowledge 的宿主适配（M3 表切换同批）只写几十行投影。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from .contracts import ChannelSnapshot, ModelConfigSnapshot


@dataclass(frozen=True)
class RegistrySQLSource:
    """宿主注入物：ORM 映射类 + 行→契约快照投影函数（映射层唯一需要宿主写的地方）。

    config_snapshot(row) -> ModelConfigSnapshot
    channel_snapshot(row) -> ChannelSnapshot（含时间列→ms 换算；model_names list→tuple）
    """

    config_mapper: type
    channel_mapper: type
    config_snapshot: Callable[[Any], ModelConfigSnapshot]
    channel_snapshot: Callable[[Any], ChannelSnapshot]


class ChannelSnapshotCache:
    """进程内渠道快照缓存：键 (tenant_id, provider|None)；provider=None 表示全量键。

    线程安全（threading.Lock）。宿主把同一实例注入多个 per-request registry
    （构造器 cache 参数）即获得进程级共享缓存；写路径对同一实例调 invalidate。
    """

    def __init__(self, ttl_ms: int):
        self.ttl_ms = ttl_ms
        self._lock = threading.Lock()
        self._items: dict[
            tuple[UUID, str | None],
            tuple[float, tuple[ChannelSnapshot, ...]],
        ] = {}

    def get(
        self, tenant_id: UUID, provider: str | None
    ) -> tuple[ChannelSnapshot, ...] | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get((tenant_id, provider))
            if item is None:
                return None
            fetched_at, snapshots = item
            if now - fetched_at >= self.ttl_ms / 1000:
                del self._items[(tenant_id, provider)]
                return None
            return snapshots

    def put(
        self,
        tenant_id: UUID,
        provider: str | None,
        snapshots: tuple[ChannelSnapshot, ...],
    ) -> None:
        with self._lock:
            self._items[(tenant_id, provider)] = (time.monotonic(), snapshots)

    def invalidate(
        self,
        tenant_id: UUID | None = None,
        provider: str | None = None,
    ) -> None:
        with self._lock:
            if tenant_id is None:
                self._items.clear()
                return
            if provider is None:
                for key in [k for k in self._items if k[0] == tenant_id]:
                    del self._items[key]
                return
            # 局部失效：点名键 + 同租户全量键（全量结果受任何渠道变更影响）
            for key in ((tenant_id, provider), (tenant_id, None)):
                self._items.pop(key, None)


def _channel_stmt(
    source: RegistrySQLSource,
    tenant_id: UUID,
    provider: str | None,
):
    mapper = source.channel_mapper
    stmt = select(mapper).where(
        mapper.tenant_id == tenant_id,
        mapper.is_active.is_(True),
    )
    if provider is not None:
        stmt = stmt.where(mapper.provider == provider)
    return stmt


class SyncSQLChannelRegistry:
    """同步态只读 registry（管理面 / worker 线程 / 迁移脚本场景）。"""

    def __init__(
        self,
        db: Session,
        source: RegistrySQLSource,
        *,
        ttl_ms: int = 60_000,
        cache: ChannelSnapshotCache | None = None,
    ):
        self.db = db
        self.source = source
        self._cache = cache if cache is not None else ChannelSnapshotCache(ttl_ms)

    def get_config(self, config_id: UUID) -> ModelConfigSnapshot | None:
        mapper = self.source.config_mapper
        row = (
            self.db.execute(
                select(mapper).where(mapper.id == config_id)
            )
            .scalars()
            .first()
        )
        return None if row is None else self.source.config_snapshot(row)

    def get_active_channels(
        self,
        tenant_id: UUID,
        provider: str | None = None,
    ) -> list[ChannelSnapshot]:
        cached = self._cache.get(tenant_id, provider)
        if cached is None:
            rows = self.db.execute(_channel_stmt(self.source, tenant_id, provider)).scalars().all()
            snapshots = tuple(self.source.channel_snapshot(row) for row in rows)
            self._cache.put(tenant_id, provider, snapshots)
            cached = snapshots
        return list(cached)

    def invalidate(
        self,
        tenant_id: UUID | None = None,
        provider: str | None = None,
    ) -> None:
        self._cache.invalidate(tenant_id, provider)


class AsyncSQLChannelRegistry:
    """异步态只读 registry（运行解析面：mem-knowledge 等 async 宿主；GC#11 纪律）。"""

    def __init__(
        self,
        db: AsyncSession,
        source: RegistrySQLSource,
        *,
        ttl_ms: int = 60_000,
        cache: ChannelSnapshotCache | None = None,
    ):
        self.db = db
        self.source = source
        self._cache = cache if cache is not None else ChannelSnapshotCache(ttl_ms)

    async def get_config(self, config_id: UUID) -> ModelConfigSnapshot | None:
        mapper = self.source.config_mapper
        result = await self.db.execute(
            select(mapper).where(mapper.id == config_id)
        )
        row = result.scalars().first()
        return None if row is None else self.source.config_snapshot(row)

    async def get_active_channels(
        self,
        tenant_id: UUID,
        provider: str | None = None,
    ) -> list[ChannelSnapshot]:
        cached = self._cache.get(tenant_id, provider)
        if cached is None:
            result = await self.db.execute(
                _channel_stmt(self.source, tenant_id, provider)
            )
            rows = result.scalars().all()
            snapshots = tuple(self.source.channel_snapshot(row) for row in rows)
            self._cache.put(tenant_id, provider, snapshots)
            cached = snapshots
        return list(cached)

    def invalidate(
        self,
        tenant_id: UUID | None = None,
        provider: str | None = None,
    ) -> None:
        self._cache.invalidate(tenant_id, provider)


__all__ = [
    "AsyncSQLChannelRegistry",
    "ChannelSnapshotCache",
    "RegistrySQLSource",
    "SyncSQLChannelRegistry",
]
