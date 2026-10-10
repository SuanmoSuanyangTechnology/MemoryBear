"""LangGraph checkpoint 的 Redis 外置存储（P0-2）。

为什么要有这个文件：协作模式（handoffs）与 workflow 引擎此前都用
`InMemorySaver`/`MemorySaver`——进程内字典。后果是

1. 重启 / 多 worker：同一会话的 handoff 历史与中断恢复点直接丢；
2. `graph_builder._checkpointer_cache` 按 thread_id 累积 `InMemorySaver` 实例，
   异常路径不清理就是内存泄漏点；
3. human intervention 的中断恢复在多 worker 下不可用（状态在另一个进程里）。

官方推荐 `AsyncPostgresSaver`，但本项目已有 Redis 基础设施（`app/aioRedis.py`）
且 checkpoint 是"会话级临时状态"而非业务数据——用 TTL 自动回收比维护一张
需要 Cron prune 的 PG 表更合适。因此这里按 `langgraph-checkpoint` 4.x 的
`BaseCheckpointSaver` 协议实现一个 Redis 后端，语义与 `InMemorySaver` 严格对齐
（三层结构：checkpoint / pending writes / channel blobs）。

**必须实现 async 方法**：`BaseCheckpointSaver` 的 `aget_tuple`/`alist`/`aput`/
`aput_writes` 默认 `raise NotImplementedError`，而 `graph.ainvoke` /
`astream_events` 走的正是 async 路径。同步方法一并实现，供 workflow 的
同步清理路径（`remove_checkpointer`）使用。

Redis 数据结构（`{prefix}` 区分调用方：cluster=协作模式，workflow=流程引擎）：

| key | 类型 | 内容 |
| --- | --- | --- |
| `{p}:{tid}:{ns}:cp` | hash | field=checkpoint_id，value=编码后的 (checkpoint, metadata, parent_id) |
| `{p}:{tid}:{ns}:idx` | zset | member=checkpoint_id，score=写入序号（取最新 = ZREVRANGE 0 0） |
| `{p}:{tid}:{ns}:w:{cid}` | hash | field=`task_id|idx`，value=编码后的 (task_id, channel, value, task_path) |
| `{p}:{tid}:{ns}:blob` | hash | field=`json([channel, version])`，value=编码后的 channel 值 |

所有 value 都是二进制（`serde.dumps_typed` 产物），因此**连接池必须
`decode_responses=False`**——不能复用 `aioRedis.aio_redis`（那是 True）。

值编码统一为 `json头 + b"\\n" + 二进制体`，头里带长度以便精确切分
（二进制体可能含任意字节，不能用分隔符扫描）。

保留策略：每次写入对所有相关 key 刷 `EXPIRE`（滑动 TTL，默认 7 天）。
这取代了官方建议的 Cron prune——会话不再活跃后自然回收，无需额外任务。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, Optional

import redis as sync_redis
import redis.asyncio as aioredis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)

# checkpoint 值上限告警阈值（Redis 单 value 上限 512MB，实际远早于此就该告警）
_BLOB_WARN_BYTES = 8 * 1024 * 1024

# 读路径遇到"值已损坏/格式不兼容"时的异常集合：JSONDecodeError、UnicodeDecodeError、
# msgpack 解包错误均为 ValueError 子类。刻意不含 Redis 连接异常——连接故障要照常上抛，
# 只有"这一条 checkpoint 读不出来"才降级（读路径跳过该条，而不是让整个会话失败）。
_CORRUPT_ERRORS = (ValueError, KeyError, IndexError)


# ───────────────────────── 二进制编解码 ─────────────────────────


def _encode_bundle(fields: dict[str, Any], payload: bytes = b"") -> bytes:
    """把 json 头 + 二进制体打包成单个 Redis value。

    头里带 `payload` 长度，读取时按长度精确切分（二进制体可能含 b"\\n"）。
    """
    fields = dict(fields)
    fields["_len"] = len(payload)
    return json.dumps(fields, ensure_ascii=False).encode("utf-8") + b"\n" + payload


def _decode_bundle(raw: bytes) -> tuple[dict[str, Any], bytes]:
    head, _, body = raw.partition(b"\n")
    fields = json.loads(head.decode("utf-8"))
    length = int(fields.pop("_len", 0) or 0)
    return fields, body[:length]


def _encode_ser(typed: tuple[str, bytes]) -> bytes:
    """序列化 `serde.dumps_typed` 的产物 `(type, bytes)`。"""
    type_name, data = typed
    return _encode_bundle({"ty": type_name}, data or b"")


def _decode_ser(raw: bytes) -> tuple[str, bytes]:
    fields, data = _decode_bundle(raw)
    return str(fields.get("ty") or ""), data


def _encode_checkpoint_bundle(
    checkpoint_typed: tuple[str, bytes],
    metadata_typed: tuple[str, bytes],
    parent_checkpoint_id: Optional[str],
) -> bytes:
    cp_ty, cp_data = checkpoint_typed
    md_ty, md_data = metadata_typed
    cp_data = cp_data or b""
    md_data = md_data or b""
    head = json.dumps(
        {
            "cp_ty": cp_ty,
            "md_ty": md_ty,
            "parent": parent_checkpoint_id,
            "_cp_len": len(cp_data),
            "_len": len(cp_data) + len(md_data),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return head + b"\n" + cp_data + md_data


def _decode_checkpoint_bundle(
    raw: bytes,
) -> tuple[tuple[str, bytes], tuple[str, bytes], Optional[str]]:
    head, _, body = raw.partition(b"\n")
    fields = json.loads(head.decode("utf-8"))
    cp_len = int(fields.get("_cp_len") or 0)
    return (
        (str(fields.get("cp_ty") or ""), body[:cp_len]),
        (str(fields.get("md_ty") or ""), body[cp_len:]),
        fields.get("parent"),
    )


def _encode_write(
    task_id: str,
    channel: str,
    value_typed: tuple[str, bytes],
    task_path: str,
) -> bytes:
    ty, data = value_typed
    data = data or b""
    head = json.dumps(
        {"task_id": task_id, "channel": channel, "task_path": task_path, "ty": ty},
        ensure_ascii=False,
    ).encode("utf-8")
    return head + b"\n" + data


def _decode_write(raw: bytes) -> tuple[str, str, tuple[str, bytes], str]:
    head, _, body = raw.partition(b"\n")
    fields = json.loads(head.decode("utf-8"))
    return (
        str(fields.get("task_id") or ""),
        str(fields.get("channel") or ""),
        (str(fields.get("ty") or ""), body),
        str(fields.get("task_path") or ""),
    )


def _blob_field(channel: str, version: Any) -> bytes:
    """blob hash 的 field：`json([channel, version])`，避免分隔符与类型歧义。"""
    return json.dumps([channel, version], ensure_ascii=False).encode("utf-8")


# ───────────────────────── thread_id 构造 ─────────────────────────

# 官方 checkpoint 后端（PG）对 thread_id 有 255 长度限制；Redis 无此限制，
# 但仍做截断——thread_id 会进 key，过长会拖累 Redis 内存与日志可读性。
_THREAD_ID_MAX_LEN = 200


def build_thread_id(
    *parts: Any,
    prefix: str = "",
    max_len: int = _THREAD_ID_MAX_LEN,
) -> str:
    """把 (tenant_id, conversation_id, ...) 拼成合法 thread_id。

    租户前缀是**隔离手段**：跨租户拿不到同一 thread_id，就读不到彼此的
    checkpoint。超长时对整体做 sha1 摘要替换，
    保证同输入稳定映射到同 thread_id。
    """
    import hashlib

    raw = ":".join(str(p) for p in parts if p not in (None, ""))
    if prefix:
        raw = f"{prefix}:{raw}"
    if len(raw) <= max_len:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    keep = max(0, max_len - len(digest) - 1)
    return f"{raw[:keep]}:{digest}" if keep else digest


# ───────────────────────── Redis 连接 ─────────────────────────

_client_lock = threading.Lock()
_async_client: Optional[aioredis.Redis] = None
_sync_client: Optional[sync_redis.Redis] = None


def _redis_url() -> str:
    from app.core.config import settings

    return f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}"


def _redis_kwargs() -> dict[str, Any]:
    from app.core.config import settings

    return {
        "db": getattr(settings, "LANGGRAPH_CHECKPOINT_REDIS_DB", settings.REDIS_DB),
        "password": settings.REDIS_PASSWORD or None,
        # checkpoint 存二进制，绝不能 decode_responses
        "decode_responses": False,
        "max_connections": getattr(settings, "LANGGRAPH_CHECKPOINT_POOL_SIZE", 20),
        "health_check_interval": 30,
        "socket_connect_timeout": 3,
        "socket_timeout": 5,
    }


def get_async_checkpoint_redis() -> aioredis.Redis:
    """异步 Redis 客户端（进程级单例，懒建连接池）。"""
    global _async_client
    if _async_client is None:
        with _client_lock:
            if _async_client is None:
                _async_client = aioredis.Redis(
                    connection_pool=aioredis.ConnectionPool.from_url(
                        _redis_url(), **_redis_kwargs()
                    )
                )
    return _async_client


def get_sync_checkpoint_redis() -> sync_redis.Redis:
    """同步 Redis 客户端（供同步清理路径使用）。"""
    global _sync_client
    if _sync_client is None:
        with _client_lock:
            if _sync_client is None:
                _sync_client = sync_redis.Redis(
                    connection_pool=sync_redis.ConnectionPool.from_url(
                        _redis_url(), **_redis_kwargs()
                    )
                )
    return _sync_client


# 异步探测的最近结果。同步路径（workflow `build_graph`）会优先复用它，
# 因为同步 ping 会阻塞事件循环 —— 能不问就不问。
_async_probe_result: Optional[bool] = None


async def checkpoint_backend_available(timeout: float = 2.0) -> bool:
    """探测 Redis 是否可用（构造期判定，不可用则由调用方降级 MemorySaver）。

    每次创建 checkpointer 都重新探测（不永久缓存）：异步 ping 开销约 1ms 且
    不阻塞事件循环，换来的是"Redis 启动后自愈"——若缓存首次失败结果，
    进程整个生命周期都用不上外置存储。

    运行中途 Redis 挂掉则不降级：那时图状态已不可信，saver 直接抛错。
    """
    global _async_probe_result
    try:
        client = get_async_checkpoint_redis()
        _async_probe_result = bool(await asyncio.wait_for(client.ping(), timeout=timeout))
    except Exception as e:
        logger.warning(f"checkpoint Redis 不可用（将降级为进程内存储）: {e}")
        _async_probe_result = False
    return _async_probe_result


# ───────────────────────── RedisSaver ─────────────────────────


class RedisCheckpointSaver(BaseCheckpointSaver[str]):
    """`InMemorySaver` 的 Redis 版：语义对齐，状态外置 + TTL 回收。

    Args:
        prefix: key 命名空间，用于区分调用方（`cluster` / `workflow`），
            避免 thread_id 撞车。
        ttl: checkpoint 滑动过期秒数。每次写入刷新，会话静默后自动回收。
        serde: 序列化器，默认沿用 base 的 `JsonPlusSerializer`。
    """

    def __init__(
        self,
        *,
        prefix: str = "cluster",
        ttl: Optional[int] = None,
        serde: Optional[SerializerProtocol] = None,
    ) -> None:
        super().__init__(serde=serde)
        self.prefix = prefix or "cluster"
        if ttl is None:
            from app.core.config import settings

            ttl = int(getattr(settings, "LANGGRAPH_CHECKPOINT_TTL", 7 * 24 * 3600))
        self.ttl = max(60, int(ttl))

    # ── key 拼装 ────────────────────────────────────────────────

    def _base(self, thread_id: str, checkpoint_ns: str) -> str:
        return f"{self.prefix}:{thread_id}:{checkpoint_ns}"

    def _cp_key(self, thread_id: str, ns: str) -> str:
        return f"{self._base(thread_id, ns)}:cp"

    def _idx_key(self, thread_id: str, ns: str) -> str:
        return f"{self._base(thread_id, ns)}:idx"

    def _writes_key(self, thread_id: str, ns: str, checkpoint_id: str) -> str:
        return f"{self._base(thread_id, ns)}:w:{checkpoint_id}"

    def _blob_key(self, thread_id: str, ns: str) -> str:
        return f"{self._base(thread_id, ns)}:blob"

    @staticmethod
    def _conf(config: RunnableConfig) -> tuple[str, str]:
        configurable = config.get("configurable") or {}
        return (
            str(configurable.get("thread_id") or ""),
            str(configurable.get("checkpoint_ns") or ""),
        )

    # ── 组装 CheckpointTuple（同步/异步共用）─────────────────────

    def _assemble_tuple(
        self,
        thread_id: str,
        ns: str,
        checkpoint_id: str,
        cp_raw: Optional[bytes],
        write_raws: Sequence[bytes],
        blob_map: dict[bytes, bytes],
        config: Optional[RunnableConfig] = None,
    ) -> Optional[CheckpointTuple]:
        """解码失败（值损坏/格式不兼容）时记警告并返回 None，由调用方跳过该条。"""
        try:
            return self._assemble_tuple_unchecked(
                thread_id, ns, checkpoint_id, cp_raw, write_raws, blob_map, config
            )
        except _CORRUPT_ERRORS as exc:
            logger.warning(
                "checkpoint 数据损坏，已跳过: thread=%s ns=%s checkpoint_id=%s error=%s",
                thread_id, ns, checkpoint_id, exc,
            )
            return None

    def _assemble_tuple_unchecked(
        self,
        thread_id: str,
        ns: str,
        checkpoint_id: str,
        cp_raw: Optional[bytes],
        write_raws: Sequence[bytes],
        blob_map: dict[bytes, bytes],
        config: Optional[RunnableConfig] = None,
    ) -> Optional[CheckpointTuple]:
        if not cp_raw:
            return None
        cp_typed, md_typed, parent_id = _decode_checkpoint_bundle(cp_raw)
        checkpoint: Checkpoint = self.serde.loads_typed(cp_typed)

        channel_values: dict[str, Any] = {}
        for channel, version in (checkpoint.get("channel_versions") or {}).items():
            raw = blob_map.get(_blob_field(channel, version))
            if raw is None:
                continue
            ty, data = _decode_ser(raw)
            if ty == "empty":
                continue
            channel_values[channel] = self.serde.loads_typed((ty, data))

        pending_writes: list[tuple[str, str, Any]] = []
        for raw in write_raws:
            if not raw:
                continue
            task_id, channel, value_typed, _path = _decode_write(raw)
            pending_writes.append((task_id, channel, self.serde.loads_typed(value_typed)))

        return CheckpointTuple(
            config=config
            or {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint={**checkpoint, "channel_values": channel_values},
            metadata=self.serde.loads_typed(md_typed),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=pending_writes,
        )

    # ── 异步实现（图执行主路径）─────────────────────────────────

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id, ns = self._conf(config)
        if not thread_id:
            return None
        client = get_async_checkpoint_redis()

        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            # 取最新：ZREVRANGE 首元素（score = 写入序号）
            latest = await client.zrevrange(self._idx_key(thread_id, ns), 0, 0)
            if not latest:
                return None
            checkpoint_id = (
                latest[0].decode("utf-8") if isinstance(latest[0], bytes) else str(latest[0])
            )

        cp_raw = await client.hget(self._cp_key(thread_id, ns), checkpoint_id)
        if not cp_raw:
            return None

        write_raws = await client.hvals(self._writes_key(thread_id, ns, checkpoint_id))
        try:
            checkpoint_typed, _md, _parent = _decode_checkpoint_bundle(cp_raw)
            blob_map = await self._aload_blobs(thread_id, ns, checkpoint_typed)
        except _CORRUPT_ERRORS as exc:
            logger.warning(
                "checkpoint 数据损坏，已跳过: thread=%s ns=%s checkpoint_id=%s error=%s",
                thread_id, ns, checkpoint_id, exc,
            )
            return None

        return self._assemble_tuple(
            thread_id, ns, checkpoint_id, cp_raw, write_raws, blob_map
        )

    async def _aload_blobs(
        self, thread_id: str, ns: str, checkpoint_typed: tuple[str, bytes]
    ) -> dict[bytes, bytes]:
        """只取本 checkpoint 需要的 channel blob（HMGET，不整表拉）。"""
        checkpoint: Checkpoint = self.serde.loads_typed(checkpoint_typed)
        versions = checkpoint.get("channel_versions") or {}
        if not versions:
            return {}
        fields = [_blob_field(k, v) for k, v in versions.items()]
        values = await get_async_checkpoint_redis().hmget(
            self._blob_key(thread_id, ns), fields
        )
        return {f: v for f, v in zip(fields, values) if v is not None}

    async def _alist_tuples(
        self,
        thread_id: str,
        ns: str,
        checkpoint_ids: list[str],
        *,
        filter: Optional[dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> list[CheckpointTuple]:
        client = get_async_checkpoint_redis()
        out: list[CheckpointTuple] = []
        remaining = limit
        for checkpoint_id in checkpoint_ids:
            if remaining is not None and remaining <= 0:
                break
            cp_raw = await client.hget(self._cp_key(thread_id, ns), checkpoint_id)
            if not cp_raw:
                continue
            try:
                cp_typed, md_typed, _parent = _decode_checkpoint_bundle(cp_raw)
                if filter:
                    metadata = self.serde.loads_typed(md_typed)
                    if not all(
                        metadata.get(k) == v for k, v in filter.items()
                    ):
                        continue
                write_raws = await client.hvals(
                    self._writes_key(thread_id, ns, checkpoint_id)
                )
                blob_map = await self._aload_blobs(thread_id, ns, cp_typed)
            except _CORRUPT_ERRORS as exc:
                logger.warning(
                    "checkpoint 数据损坏，已跳过: thread=%s ns=%s checkpoint_id=%s error=%s",
                    thread_id, ns, checkpoint_id, exc,
                )
                continue
            tup = self._assemble_tuple(thread_id, ns, checkpoint_id, cp_raw, write_raws, blob_map)
            if tup is not None:
                out.append(tup)
                if remaining is not None:
                    remaining -= 1
        return out

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        client = get_async_checkpoint_redis()

        if config is None:
            # 全库遍历（`get_state_history(None)`）：本项目调用方总是带 thread_id，
            # 走到这里说明用法异常，SCAN 全库代价大，记警告后返回空。
            logger.warning("RedisCheckpointSaver.alist 收到 config=None，跳过全库遍历")
            return

        thread_id, ns = self._conf(config)
        if not thread_id:
            return

        ids = await client.zrevrange(self._idx_key(thread_id, ns), 0, -1)
        checkpoint_ids = [i.decode("utf-8") if isinstance(i, bytes) else str(i) for i in ids]

        before_id = get_checkpoint_id(before) if before else None
        if before_id:
            # checkpoint_id 为 uuid6，字符串序即时间序（InMemorySaver 同此假设）
            checkpoint_ids = [cid for cid in checkpoint_ids if cid < before_id]

        config_checkpoint_id = get_checkpoint_id(config)
        if config_checkpoint_id:
            checkpoint_ids = [cid for cid in checkpoint_ids if cid == config_checkpoint_id]

        for tup in await self._alist_tuples(
            thread_id, ns, checkpoint_ids, filter=filter, limit=limit
        ):
            yield tup

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, ns = self._conf(config)
        if not thread_id:
            raise ValueError("checkpoint 写入缺少 thread_id")

        c = checkpoint.copy()
        values: dict[str, Any] = c.pop("channel_values")  # type: ignore[misc]
        parent_id = (config.get("configurable") or {}).get("checkpoint_id")

        cp_bundle = _encode_checkpoint_bundle(
            self.serde.dumps_typed(c),
            self.serde.dumps_typed(get_checkpoint_metadata(config, metadata)),
            parent_id,
        )

        client = get_async_checkpoint_redis()
        pipe = client.pipeline(transaction=True)
        cp_key = self._cp_key(thread_id, ns)
        idx_key = self._idx_key(thread_id, ns)
        blob_key = self._blob_key(thread_id, ns)

        pipe.hset(cp_key, checkpoint["id"], cp_bundle)
        # 所有成员 score 恒为 0：Redis 对同分成员按字典序排，而 LangGraph 的
        # checkpoint id 是单调递增的 uuid6（字符串序 = 时间序，InMemorySaver 也是
        # max(id) 取最新）。ZREVRANGE 首元素即最新，无需 ZCARD 序号，
        # 因此不存在并发写分值相同、prune 后分值回退的问题。
        pipe.zadd(idx_key, {checkpoint["id"]: 0}, nx=True)
        for channel, version in (new_versions or {}).items():
            typed = (
                self.serde.dumps_typed(values[channel])
                if channel in values
                else ("empty", b"")
            )
            encoded = _encode_ser(typed)
            if len(encoded) > _BLOB_WARN_BYTES:
                logger.warning(
                    f"checkpoint channel 值过大: thread={thread_id} "
                    f"channel={channel} bytes={len(encoded)}"
                )
            pipe.hset(blob_key, _blob_field(channel, version), encoded)
        pipe.expire(cp_key, self.ttl)
        pipe.expire(idx_key, self.ttl)
        pipe.expire(blob_key, self.ttl)
        await pipe.execute()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, ns = self._conf(config)
        checkpoint_id = (config.get("configurable") or {}).get("checkpoint_id")
        if not thread_id or not checkpoint_id:
            raise ValueError("put_writes 缺少 thread_id / checkpoint_id")

        key = self._writes_key(thread_id, ns, str(checkpoint_id))
        client = get_async_checkpoint_redis()
        pipe = client.pipeline(transaction=True)
        for idx, (channel, value) in enumerate(writes):
            mapped = WRITES_IDX_MAP.get(channel, idx)
            field = f"{task_id}|{mapped}"
            encoded = _encode_write(
                task_id, channel, self.serde.dumps_typed(value), task_path
            )
            if mapped >= 0:
                # 与 InMemorySaver 一致：正向 idx 幂等，已存在不覆盖
                pipe.hsetnx(key, field, encoded)
            else:
                # 特殊写（ERROR/SCHEDULED/INTERRUPT/RESUME）始终覆盖
                pipe.hset(key, field, encoded)
        pipe.expire(key, self.ttl)
        await pipe.execute()

    async def adelete_thread(self, thread_id: str) -> None:
        if not thread_id:
            return
        client = get_async_checkpoint_redis()
        cursor = 0
        pattern = f"{self.prefix}:{thread_id}:*"
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                await client.delete(*keys)
            if cursor == 0:
                break

    async def aprune(
        self, thread_ids: Sequence[str], *, strategy: str = "keep_latest"
    ) -> None:
        """按策略回收：`delete` 全删，`keep_latest` 只留每个 ns 最新一条。"""
        client = get_async_checkpoint_redis()
        for thread_id in thread_ids:
            if strategy == "delete":
                await self.adelete_thread(thread_id)
                continue
            cursor = 0
            idx_pattern = f"{self.prefix}:{thread_id}:*:idx"
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=idx_pattern, count=100)
                for idx_key in keys:
                    idx_key = (
                        idx_key.decode("utf-8") if isinstance(idx_key, bytes) else str(idx_key)
                    )
                    stale = await client.zrevrange(idx_key, 1, -1)
                    if not stale:
                        continue
                    base = idx_key[: -len(":idx")]
                    pipe = client.pipeline(transaction=False)
                    for cid in stale:
                        cid = cid.decode("utf-8") if isinstance(cid, bytes) else str(cid)
                        pipe.hdel(f"{base}:cp", cid)
                        pipe.delete(f"{base}:w:{cid}")
                        pipe.zrem(idx_key, cid)
                    await pipe.execute()
                if cursor == 0:
                    break

    # ── 同步实现（清理路径 / 同步图调用）────────────────────────

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id, ns = self._conf(config)
        if not thread_id:
            return None
        client = get_sync_checkpoint_redis()

        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            latest = client.zrevrange(self._idx_key(thread_id, ns), 0, 0)
            if not latest:
                return None
            checkpoint_id = (
                latest[0].decode("utf-8") if isinstance(latest[0], bytes) else str(latest[0])
            )

        cp_raw = client.hget(self._cp_key(thread_id, ns), checkpoint_id)
        if not cp_raw:
            return None

        write_raws = client.hvals(self._writes_key(thread_id, ns, checkpoint_id))
        try:
            checkpoint_typed, _md, _parent = _decode_checkpoint_bundle(cp_raw)
            checkpoint: Checkpoint = self.serde.loads_typed(checkpoint_typed)
        except _CORRUPT_ERRORS as exc:
            logger.warning(
                "checkpoint 数据损坏，已跳过: thread=%s ns=%s checkpoint_id=%s error=%s",
                thread_id, ns, checkpoint_id, exc,
            )
            return None
        versions = checkpoint.get("channel_versions") or {}
        blob_map: dict[bytes, bytes] = {}
        if versions:
            fields = [_blob_field(k, v) for k, v in versions.items()]
            values = client.hmget(self._blob_key(thread_id, ns), fields)
            blob_map = {f: v for f, v in zip(fields, values) if v is not None}

        return self._assemble_tuple(
            thread_id, ns, checkpoint_id, cp_raw, write_raws, blob_map
        )

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            logger.warning("RedisCheckpointSaver.list 收到 config=None，跳过全库遍历")
            return
        thread_id, ns = self._conf(config)
        if not thread_id:
            return
        client = get_sync_checkpoint_redis()
        ids = client.zrevrange(self._idx_key(thread_id, ns), 0, -1)
        checkpoint_ids = [i.decode("utf-8") if isinstance(i, bytes) else str(i) for i in ids]

        before_id = get_checkpoint_id(before) if before else None
        if before_id:
            checkpoint_ids = [cid for cid in checkpoint_ids if cid < before_id]
        config_checkpoint_id = get_checkpoint_id(config)
        if config_checkpoint_id:
            checkpoint_ids = [cid for cid in checkpoint_ids if cid == config_checkpoint_id]

        remaining = limit
        for checkpoint_id in checkpoint_ids:
            if remaining is not None and remaining <= 0:
                break
            cp_raw = client.hget(self._cp_key(thread_id, ns), checkpoint_id)
            if not cp_raw:
                continue
            try:
                cp_typed, md_typed, _parent = _decode_checkpoint_bundle(cp_raw)
                if filter:
                    metadata = self.serde.loads_typed(md_typed)
                    if not all(metadata.get(k) == v for k, v in filter.items()):
                        continue
                checkpoint_: Checkpoint = self.serde.loads_typed(cp_typed)
            except _CORRUPT_ERRORS as exc:
                logger.warning(
                    "checkpoint 数据损坏，已跳过: thread=%s ns=%s checkpoint_id=%s error=%s",
                    thread_id, ns, checkpoint_id, exc,
                )
                continue
            write_raws = client.hvals(self._writes_key(thread_id, ns, checkpoint_id))
            versions = checkpoint_.get("channel_versions") or {}
            blob_map: dict[bytes, bytes] = {}
            if versions:
                fields = [_blob_field(k, v) for k, v in versions.items()]
                values = client.hmget(self._blob_key(thread_id, ns), fields)
                blob_map = {f: v for f, v in zip(fields, values) if v is not None}
            tup = self._assemble_tuple(
                thread_id, ns, checkpoint_id, cp_raw, write_raws, blob_map
            )
            if tup is not None:
                yield tup
                if remaining is not None:
                    remaining -= 1

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, ns = self._conf(config)
        if not thread_id:
            raise ValueError("checkpoint 写入缺少 thread_id")

        c = checkpoint.copy()
        values: dict[str, Any] = c.pop("channel_values")  # type: ignore[misc]
        parent_id = (config.get("configurable") or {}).get("checkpoint_id")

        cp_bundle = _encode_checkpoint_bundle(
            self.serde.dumps_typed(c),
            self.serde.dumps_typed(get_checkpoint_metadata(config, metadata)),
            parent_id,
        )

        client = get_sync_checkpoint_redis()
        pipe = client.pipeline(transaction=True)
        cp_key = self._cp_key(thread_id, ns)
        idx_key = self._idx_key(thread_id, ns)
        blob_key = self._blob_key(thread_id, ns)

        pipe.hset(cp_key, checkpoint["id"], cp_bundle)
        pipe.zadd(idx_key, {checkpoint["id"]: 0}, nx=True)
        for channel, version in (new_versions or {}).items():
            typed = (
                self.serde.dumps_typed(values[channel])
                if channel in values
                else ("empty", b"")
            )
            pipe.hset(blob_key, _blob_field(channel, version), _encode_ser(typed))
        pipe.expire(cp_key, self.ttl)
        pipe.expire(idx_key, self.ttl)
        pipe.expire(blob_key, self.ttl)
        pipe.execute()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, ns = self._conf(config)
        checkpoint_id = (config.get("configurable") or {}).get("checkpoint_id")
        if not thread_id or not checkpoint_id:
            raise ValueError("put_writes 缺少 thread_id / checkpoint_id")

        key = self._writes_key(thread_id, ns, str(checkpoint_id))
        client = get_sync_checkpoint_redis()
        pipe = client.pipeline(transaction=True)
        for idx, (channel, value) in enumerate(writes):
            mapped = WRITES_IDX_MAP.get(channel, idx)
            field = f"{task_id}|{mapped}"
            encoded = _encode_write(
                task_id, channel, self.serde.dumps_typed(value), task_path
            )
            if mapped >= 0:
                pipe.hsetnx(key, field, encoded)
            else:
                pipe.hset(key, field, encoded)
        pipe.expire(key, self.ttl)
        pipe.execute()

    def delete_thread(self, thread_id: str) -> None:
        if not thread_id:
            return
        client = get_sync_checkpoint_redis()
        cursor = 0
        pattern = f"{self.prefix}:{thread_id}:*"
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                client.delete(*keys)
            if cursor == 0:
                break


# ───────────────────────── 工厂 ─────────────────────────


def _redis_enabled() -> bool:
    from app.core.config import settings

    return str(getattr(settings, "LANGGRAPH_CHECKPOINT_BACKEND", "redis")).lower() != "memory"


async def create_async_checkpointer(
    *,
    prefix: str = "cluster",
    ttl: Optional[int] = None,
) -> Any:
    """创建异步 checkpointer：Redis 可用则外置，不可用降级 `InMemorySaver`。

    降级只发生在**创建时**（探测失败 = 行为等同改造前），运行中途 Redis 异常
    会直接抛出——那时状态已不可信，静默继续会产出错乱结果。
    """
    if _redis_enabled() and await checkpoint_backend_available():
        return RedisCheckpointSaver(prefix=prefix, ttl=ttl)
    return InMemorySaver()


def create_sync_checkpointer(*, prefix: str = "workflow", ttl: Optional[int] = None) -> Any:
    """创建同步 checkpointer（workflow 引擎路径），同样带降级。

    探测有代价：`build_graph` 是同步方法（从 async 执行链里调），ping 会阻塞
    事件循环。因此

    1. 探测结果**进程级缓存**——最多阻塞一次；
    2. 用独立的短超时连接（1.5s）而不是复用带 3s 超时的业务连接；
    3. 已有异步探测结果时直接复用，一次阻塞都不发生。

    Redis 起来之后中途挂掉不会触发降级（缓存已定型），此时 saver 会抛错——
    图状态已不可信，静默换后端只会产出错乱结果。
    """
    if not _redis_enabled():
        return InMemorySaver()

    available = _probe_once_sync()
    if not available:
        return InMemorySaver()
    return RedisCheckpointSaver(prefix=prefix, ttl=ttl)


_sync_probe_result: Optional[bool] = None


def _probe_once_sync(timeout: float = 1.5) -> bool:
    """进程级一次性同步探测（复用异步探测的结果，避免重复阻塞）。"""
    global _sync_probe_result
    if _async_probe_result is not None:
        return _async_probe_result
    if _sync_probe_result is not None:
        return _sync_probe_result
    with _client_lock:
        if _sync_probe_result is None:
            try:
                from app.core.config import settings

                client = sync_redis.Redis(
                    host=settings.REDIS_HOST,
                    port=settings.REDIS_PORT,
                    **{
                        **_redis_kwargs(),
                        "socket_connect_timeout": timeout,
                        "socket_timeout": timeout,
                    },
                )
                _sync_probe_result = bool(client.ping())
                client.close()
            except Exception as e:
                logger.warning(f"checkpoint Redis 同步探测失败（降级进程内存储）: {e}")
                _sync_probe_result = False
    return _sync_probe_result
