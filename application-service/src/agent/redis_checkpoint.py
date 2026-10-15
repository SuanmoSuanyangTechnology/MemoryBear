"""Redis-backed external store for LangGraph checkpoints.

Why this file exists: the handoff mode and the workflow engine both used
`InMemorySaver`/`MemorySaver` before -- an in-process dictionary. Consequences:

1. Restart / multi-worker: a session's handoff history and interrupt recovery
   points are lost outright;
2. `graph_builder._checkpointer_cache` accumulates `InMemorySaver` instances
   keyed by thread_id and never cleans them on error paths -- a memory leak;
3. Human-intervention interrupt recovery is unusable with multiple workers
   (the state lives in another process).

The official recommendation is `AsyncPostgresSaver`, but this project already
has Redis infrastructure (`app/aioRedis.py`), and checkpoints are "session-level
transient state" rather than business data -- TTL-based automatic reclamation
beats maintaining a PG table that needs a Cron prune. This module therefore
implements a Redis backend against the `langgraph-checkpoint` 4.x
`BaseCheckpointSaver` protocol, with semantics strictly aligned to
`InMemorySaver` (three layers: checkpoint / pending writes / channel blobs).

**The async methods must be implemented**: `BaseCheckpointSaver`'s `aget_tuple`/
`alist`/`aput`/`aput_writes` default to `raise NotImplementedError`, while
`graph.ainvoke` / `astream_events` take exactly the async path. The synchronous
methods are implemented as well, for the workflow engine's synchronous cleanup
path (`remove_checkpointer`).

Redis data layout (`{prefix}` distinguishes callers: cluster=handoff mode,
workflow=flow engine):

| key | type | content |
| --- | --- | --- |
| `{p}:{tid}:{ns}:cp` | hash | field=checkpoint_id, value=encoded (checkpoint, metadata, parent_id) |
| `{p}:{tid}:{ns}:idx` | zset | member=checkpoint_id, score=write order (latest = ZREVRANGE 0 0) |
| `{p}:{tid}:{ns}:w:{cid}` | hash | field=`task_id|idx`, value=encoded (task_id, channel, value, task_path) |
| `{p}:{tid}:{ns}:blob` | hash | field=`json([channel, version])`, value=encoded channel value |

All values are binary (`serde.dumps_typed` output), so **the connection pool
must use `decode_responses=False`** -- `aioRedis.aio_redis` cannot be reused
(it sets True).

Values are uniformly encoded as `json header + b"\\n" + binary body`, with the
length carried in the header for exact splitting (the binary body may contain
arbitrary bytes and cannot be scanned with a delimiter).

Retention: every write refreshes `EXPIRE` on all related keys (sliding TTL,
default 7 days). This replaces the officially suggested Cron prune -- sessions
are reclaimed naturally once inactive, no extra job needed.
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

# Warn threshold for a checkpoint value (Redis caps a single value at 512MB; warn well before that)
_BLOB_WARN_BYTES = 8 * 1024 * 1024

# Exceptions treated as "corrupt value / incompatible format" on the read path:
# JSONDecodeError, UnicodeDecodeError, and msgpack unpack errors are all ValueError
# subclasses. Redis connection errors are deliberately excluded -- connection failures
# must propagate as usual; only "this one checkpoint cannot be read" degrades (the read
# path skips that entry instead of failing the whole session).
_CORRUPT_ERRORS = (ValueError, KeyError, IndexError)


# ───────────────────────── binary encode/decode ─────────────────────────


def _encode_bundle(fields: dict[str, Any], payload: bytes = b"") -> bytes:
    """Pack a json header + binary body into a single Redis value.

    The header carries the `payload` length so the read side splits exactly
    (the binary body may contain b"\\n").
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
    """Serialize the `(type, bytes)` output of `serde.dumps_typed`."""
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
    """Field of the blob hash: `json([channel, version])`, avoiding delimiter and type ambiguity."""
    return json.dumps([channel, version], ensure_ascii=False).encode("utf-8")


# ───────────────────────── thread_id construction ─────────────────────────

# The official checkpoint backend (PG) limits thread_id to 255 chars; Redis has
# no such limit, but truncation is still applied -- thread_id goes into keys, and
# overly long ones bloat Redis memory and hurt log readability.
_THREAD_ID_MAX_LEN = 200


def build_thread_id(
    *parts: Any,
    prefix: str = "",
    max_len: int = _THREAD_ID_MAX_LEN,
) -> str:
    """Join (tenant_id, conversation_id, ...) into a valid thread_id.

    The tenant prefix is an **isolation mechanism**: without the same thread_id,
    cross-tenant reads cannot reach each other's checkpoints. When the result is
    too long, a sha1 digest replaces the whole, mapping identical inputs stably
    to the same thread_id.
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


# ───────────────────────── Redis connections ─────────────────────────

_client_lock = threading.Lock()
_async_client: Optional[aioredis.Redis] = None
_sync_client: Optional[sync_redis.Redis] = None


def _redis_url() -> str:
    from ..config import current_settings

    return f"redis://{current_settings().redis_host}:{current_settings().redis_port}"


def _redis_kwargs() -> dict[str, Any]:
    from ..config import current_settings

    return {
        "db": (
            current_settings().langgraph_checkpoint_redis_db
            if current_settings().langgraph_checkpoint_redis_db is not None
            else current_settings().redis_db
        ),
        "password": current_settings().redis_password.get_secret_value() or None,
        # Checkpoints are stored as binary; decode_responses must stay off
        "decode_responses": False,
        "max_connections": current_settings().langgraph_checkpoint_pool_size,
        "health_check_interval": 30,
        "socket_connect_timeout": 3,
        "socket_timeout": 5,
    }


def get_async_checkpoint_redis() -> aioredis.Redis:
    """Async Redis client (process-level singleton, connection pool built lazily)."""
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
    """Sync Redis client (used by the synchronous cleanup path)."""
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


# Latest result of the async probe. The sync path (workflow `build_graph`) reuses
# it preferentially, because a sync ping blocks the event loop -- avoid it if possible.
_async_probe_result: Optional[bool] = None


async def checkpoint_backend_available(timeout: float = 2.0) -> bool:
    """Probe whether Redis is available (decided at construction time; callers degrade to MemorySaver when down).

    Every checkpointer creation re-probes (no permanent cache): an async ping
    costs about 1ms and does not block the event loop, and it buys "self-healing
    once Redis comes up" -- caching the first failure would leave the whole
    process lifetime without the external store.

    Redis failing mid-run does not trigger degradation: graph state is no longer
    trustworthy at that point, and the saver raises directly.
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
    """Redis version of `InMemorySaver`: aligned semantics, external state + TTL reclamation.

    Args:
        prefix: Key namespace distinguishing callers (`cluster` / `workflow`) to
            avoid thread_id collisions.
        ttl: Sliding expiration in seconds for checkpoints. Refreshed on every
            write; reclaimed once the session goes quiet.
        serde: Serializer; defaults to the base `JsonPlusSerializer`.
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
            from ..config import current_settings

            ttl = int(current_settings().langgraph_checkpoint_ttl)
        self.ttl = max(60, int(ttl))

    # ── key assembly ────────────────────────────────────────────

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

    # ── building the CheckpointTuple (shared by sync/async) ─────

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
        """On decode failure (corrupt value / incompatible format), warn and return None; the caller skips the entry."""
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

    # ── async implementation (main graph-execution path) ────────

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id, ns = self._conf(config)
        if not thread_id:
            return None
        client = get_async_checkpoint_redis()

        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            # Latest = first ZREVRANGE element (score = write order)
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
        """Fetch only the channel blobs this checkpoint needs (HMGET, not the whole hash)."""
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
            # Full-store traversal (`get_state_history(None)`): callers in this project
            # always pass thread_id, so reaching here is abnormal usage; a full-store
            # SCAN is expensive, so log a warning and return nothing.
            logger.warning("RedisCheckpointSaver.alist 收到 config=None，跳过全库遍历")
            return

        thread_id, ns = self._conf(config)
        if not thread_id:
            return

        ids = await client.zrevrange(self._idx_key(thread_id, ns), 0, -1)
        checkpoint_ids = [i.decode("utf-8") if isinstance(i, bytes) else str(i) for i in ids]

        before_id = get_checkpoint_id(before) if before else None
        if before_id:
            # checkpoint_id is uuid6, string order equals time order (InMemorySaver assumes the same)
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
        # All members keep score 0 on purpose: Redis orders equal-score members
        # lexicographically, and LangGraph checkpoint ids are monotonically increasing
        # uuid6 (string order = time order; InMemorySaver likewise takes max(id) as
        # latest). The first ZREVRANGE element is the latest, so no ZCARD counter is
        # needed -- and there is no risk from equal scores under concurrent writes or
        # from score rollback after pruning.
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
                # Match InMemorySaver: positive idx is idempotent, existing fields are not overwritten
                pipe.hsetnx(key, field, encoded)
            else:
                # Special writes (ERROR/SCHEDULED/INTERRUPT/RESUME) always overwrite
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
        """Reclaim by strategy: `delete` removes everything, `keep_latest` keeps only the newest entry per ns."""
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

    # ── sync implementation (cleanup path / synchronous graph calls) ──

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


# ───────────────────────── factory ─────────────────────────


def _redis_enabled() -> bool:
    from ..config import current_settings

    return str(current_settings().langgraph_checkpoint_backend).lower() != "memory"


async def create_async_checkpointer(
    *,
    prefix: str = "cluster",
    ttl: Optional[int] = None,
) -> Any:
    """Create an async checkpointer: Redis when available, else degrade to `InMemorySaver`.

    Degradation happens **at creation time only** (a failed probe means behavior
    as before); a mid-run Redis failure raises directly -- state is no longer
    trustworthy then, and continuing silently would produce corrupt results.
    """
    if _redis_enabled() and await checkpoint_backend_available():
        return RedisCheckpointSaver(prefix=prefix, ttl=ttl)
    return InMemorySaver()


def create_sync_checkpointer(*, prefix: str = "workflow", ttl: Optional[int] = None) -> Any:
    """Create the sync checkpointer (workflow engine path), also with degradation.

    Probing has a cost: `build_graph` is a synchronous method (called from the
    async execution chain), and a ping blocks the event loop. Therefore

    1. the probe result is **cached at process level** -- at most one block;
    2. a dedicated short-timeout connection (1.5s) is used instead of reusing the
       business connection with its 3s timeout;
    3. an existing async probe result is reused directly, so no block happens at all.

    Redis failing mid-run after startup does not trigger degradation (the cache is
    already fixed); the saver raises instead -- graph state is no longer
    trustworthy, and silently switching backends would only produce corrupt results.
    """
    if not _redis_enabled():
        return InMemorySaver()

    available = _probe_once_sync()
    if not available:
        return InMemorySaver()
    return RedisCheckpointSaver(prefix=prefix, ttl=ttl)


_sync_probe_result: Optional[bool] = None


def _probe_once_sync(timeout: float = 1.5) -> bool:
    """One-shot process-level sync probe (reuses the async probe result to avoid repeated blocking)."""
    global _sync_probe_result
    if _async_probe_result is not None:
        return _async_probe_result
    if _sync_probe_result is not None:
        return _sync_probe_result
    with _client_lock:
        if _sync_probe_result is None:
            try:
                from ..config import current_settings

                client = sync_redis.Redis(
                    host=current_settings().redis_host,
                    port=current_settings().redis_port,
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
