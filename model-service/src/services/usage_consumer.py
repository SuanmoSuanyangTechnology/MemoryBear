"""用量事件消费常驻任务（spec §13.2；B6 自宿主 app/services/usage_consumer.py 迁入）。

- 常驻 ``XREADGROUP`` 长轮询（block 5s）取代宿主 Celery beat 的 5s 周期触发；服务无 celery
- 消费组 ``model-usage-consumers`` 不变：多副本同组天然分摊（``>`` 只投给发问消费者，
  consumer 名 hostname-pid 保证每进程唯一）；组已存在即 BUSYGROUP 吞掉，位点从组级
  last-delivered-id 续读——宿主停 beat 后本进程接手，宿主遗留 pending 由 XAUTOCLAIM 接管
- ``XAUTOCLAIM`` 兜底：pending idle > 60s 的消息（落表失败/进程崩溃遗留）重新领取处理
- 幂等：``ON CONFLICT (event_id) DO NOTHING``，重复投递不重复落表、照常 ACK
- 坏消息（payload 缺失/JSON 非法/字段类型错）记 warning 后 ACK，避免毒丸阻塞消费组
- 落表失败（DB 短暂不可用）不 ACK：消息留在 pending 由下一轮 XAUTOCLAIM 重领，
  本轮告警 + 退避后继续（避免 DB 长挂时热转）
- Redis 层异常冒泡外层：按 channel_registry 口径 1s→30s 退避重连；取消直抛

旁路铁律：本模块只读 stream 与写 model_usage_records，失败不影响 invoke 调用链路。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import current_settings
from ..infrastructure.redis import get_async_client
from ..models.model_usage_record import ModelUsageRecord
from .usage_publisher import MODEL_USAGE_STREAM

logger = logging.getLogger(__name__)

MODEL_USAGE_CONSUMER_GROUP = "model-usage-consumers"

_BATCH_SIZE = 200
_BLOCK_MS = 5_000
_RECLAIM_MIN_IDLE_MS = 60_000
_DB_ERROR_SLEEP_S = 2.0
_RECONNECT_BASE_DELAY_S = 1.0
_RECONNECT_MAX_DELAY_S = 30.0
_CAPABILITIES = ("llm", "embedding", "rerank", "image", "video", "asr")
_STATUSES = ("ok", "fallback_succeeded", "failed")
_REQUIRED_FIELDS = (
    "event_id",
    "ts_ms",
    "tenant_id",
    "config_id",
    "provider",
    "model_name",
    "capability",
    "status",
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


async def _ensure_group(client: Redis) -> None:
    try:
        await client.xgroup_create(
            MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP, id="$", mkstream=True
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _to_uuid(value: Any, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid uuid field {field}={value!r}") from exc


def _to_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid int field {field}={value!r}") from exc


def _optional_int(value: Any, field: str) -> int | None:
    return None if value is None else _to_int(value, field)


def _to_datetime(value: Any, field: str) -> datetime:
    """事件毫秒时间戳 → naive UTC datetime（与项目 utcnow_naive 惯例一致）。"""
    ms = _to_int(value, field)
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"invalid timestamp field {field}={value!r}") from exc


def _row_from_payload(payload: str) -> dict[str, Any]:
    """事件 JSON → model_usage_records 行；字段非法抛 ValueError（调用方按坏消息 ACK）。"""
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("payload is not a JSON object")
    missing = [key for key in _REQUIRED_FIELDS if data.get(key) is None]
    if missing:
        raise ValueError(f"missing required fields: {','.join(missing)}")
    # 大小写归一（枚举值恒小写，容忍外来大写形态）：落表统一小写口径，杜绝混行双值
    capability = str(data["capability"]).lower()
    if capability not in _CAPABILITIES:
        raise ValueError(f"unknown capability {capability!r}")
    status = str(data["status"])
    if status not in _STATUSES:
        raise ValueError(f"unknown status {status!r}")
    return {
        "event_id": _to_uuid(data["event_id"], "event_id"),
        "source_service": str(data.get("source_service") or "unknown")[:32],
        "tenant_id": _to_uuid(data["tenant_id"], "tenant_id"),
        "config_id": _to_uuid(data["config_id"], "config_id"),
        "channel_id": None
        if data.get("channel_id") is None
        else _to_uuid(data["channel_id"], "channel_id"),
        "resource_type": None
        if data.get("resource_type") is None
        else str(data["resource_type"])[:32],
        "resource_id": None
        if data.get("resource_id") is None
        else _to_uuid(data["resource_id"], "resource_id"),
        "provider": str(data["provider"])[:50],
        "model_name": str(data["model_name"])[:255],
        "capability": capability,
        "stream": bool(data.get("stream", False)),
        "input_tokens": 0
        if data.get("input_tokens") is None
        else _to_int(data["input_tokens"], "input_tokens"),
        "output_tokens": 0
        if data.get("output_tokens") is None
        else _to_int(data["output_tokens"], "output_tokens"),
        "images_count": _optional_int(data.get("images_count"), "images_count"),
        "latency_ms": 0
        if data.get("latency_ms") is None
        else _to_int(data["latency_ms"], "latency_ms"),
        "status": status,
        "error_type": None if data.get("error_type") is None else str(data["error_type"])[:64],
        "attempts": max(
            1, 1 if data.get("attempts") is None else _to_int(data["attempts"], "attempts")
        ),
        "request_id": None if data.get("request_id") is None else str(data["request_id"])[:64],
        "created_at": _to_datetime(data["ts_ms"], "ts_ms"),
    }


async def _insert_rows(session_factory: SessionFactory, rows: list[dict[str, Any]]) -> int:
    """批量幂等落表，返回实际新插入行数（重复 event_id 跳过）。"""
    if not rows:
        return 0
    statement = (
        pg_insert(ModelUsageRecord)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(ModelUsageRecord.event_id)
    )
    async with session_factory() as db:
        result = await db.execute(statement)
        inserted = result.scalars().all()
        await db.commit()
    return len(inserted)


async def _handle_entries(
    entries: list[tuple[str, dict[str, str]]],
    session_factory: SessionFactory,
    stats: dict[str, int],
) -> list[str]:
    """解析 → 落表 → 返回可 ACK 的消息 id（坏消息也 ACK；落表异常向上抛，不 ACK）。"""
    ack_ids: list[str] = []
    rows: list[dict[str, Any]] = []
    for message_id, fields in entries:
        stats["read"] += 1
        payload = fields.get("payload")
        if not payload:
            stats["malformed"] += 1
            logger.warning("用量消息缺 payload，已丢弃: id=%s", message_id)
            ack_ids.append(message_id)
            continue
        try:
            rows.append(_row_from_payload(payload))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            stats["malformed"] += 1
            logger.warning("用量消息非法，已丢弃: id=%s err=%s", message_id, exc)
            ack_ids.append(message_id)
            continue
        ack_ids.append(message_id)
    stats["inserted"] += await _insert_rows(session_factory, rows)
    return ack_ids


async def _read_new(client: Redis, batch_size: int) -> list[tuple[str, dict[str, str]]]:
    response = await client.xreadgroup(
        MODEL_USAGE_CONSUMER_GROUP,
        _consumer_name(),
        {MODEL_USAGE_STREAM: ">"},
        count=batch_size,
        block=_BLOCK_MS,
    )
    entries: list[tuple[str, dict[str, str]]] = []
    for _stream, messages in response or []:
        entries.extend(messages)
    return entries


async def _reclaim_stale(
    client: Redis, session_factory: SessionFactory, stats: dict[str, int]
) -> None:
    """XAUTOCLAIM 兜底：领取 idle 超阈的 pending 消息（落表失败/进程崩溃遗留）。"""
    cursor = "0-0"
    while True:
        try:
            claimed = await client.xautoclaim(
                MODEL_USAGE_STREAM,
                MODEL_USAGE_CONSUMER_GROUP,
                _consumer_name(),
                min_idle_time=_RECLAIM_MIN_IDLE_MS,
                start_id=cursor,
                count=_BATCH_SIZE,
            )
        except ResponseError as exc:
            logger.warning("XAUTOCLAIM 失败（忽略，下轮重试）: %s", exc)
            return
        cursor, messages = claimed[0], claimed[1]
        if messages:
            stats["reclaimed"] += len(messages)
            ack_ids = await _handle_entries(messages, session_factory, stats)
            if ack_ids:
                await client.xack(MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP, *ack_ids)
        if not messages or cursor in ("0-0", "0", None):
            return


async def _warn_on_backlog(client: Redis) -> None:
    threshold = current_settings().model_usage_backlog_warn
    try:
        length = await client.xlen(MODEL_USAGE_STREAM)
    except RedisError as exc:
        logger.warning("用量 stream 长度查询失败: %s", exc)
        return
    if length and length > threshold:
        logger.warning("用量 stream 积压: xlen=%d 超阈值 %d", length, threshold)
    try:
        pending = await client.xpending(MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP)
    except ResponseError:
        return
    pending_count = pending.get("pending") if isinstance(pending, dict) else None
    if pending_count and pending_count > threshold:
        logger.warning("用量消费组 pending 积压: %d 超阈值 %d", pending_count, threshold)


async def _consume_forever(client: Redis, session_factory: SessionFactory) -> None:
    """内层无限循环：积压检查 → 领遗留 → block 读新消息 → 落表 → ACK。

    落表异常不 ACK（消息留 pending，60s 后由 XAUTOCLAIM 重领）：本轮告警退避后继续；
    Redis 层异常（含 ACK 失败）冒泡给外层退避重连。
    """
    while True:
        stats = {"read": 0, "inserted": 0, "malformed": 0, "reclaimed": 0}
        try:
            await _warn_on_backlog(client)
            await _reclaim_stale(client, session_factory, stats)
            entries = await _read_new(client, _BATCH_SIZE)
            if entries:
                ack_ids = await _handle_entries(entries, session_factory, stats)
                if ack_ids:
                    await client.xack(MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP, *ack_ids)
        except RedisError:
            raise
        except Exception as exc:
            logger.warning("用量落表失败，消息留 pending 待 XAUTOCLAIM 重领: %s", exc)
            await asyncio.sleep(_DB_ERROR_SLEEP_S)
            continue
        if stats["read"] or stats["reclaimed"]:
            logger.info(
                "用量消费: read=%d inserted=%d malformed=%d reclaimed=%d",
                stats["read"],
                stats["inserted"],
                stats["malformed"],
                stats["reclaimed"],
            )


async def run_usage_consumer(session_factory: SessionFactory) -> None:
    """常驻消费入口（lifespan 挂载）：Redis 不可用时退避重连，不阻断启动与请求。

    取消（进程收尾）即退出：block 读被取消时 redis-py 断连重抛，退避不吞取消。
    """
    delay = _RECONNECT_BASE_DELAY_S
    while True:
        try:
            client = await get_async_client()
            await _ensure_group(client)
            delay = _RECONNECT_BASE_DELAY_S
            logger.info(
                "用量消费就绪: stream=%s group=%s consumer=%s",
                MODEL_USAGE_STREAM,
                MODEL_USAGE_CONSUMER_GROUP,
                _consumer_name(),
            )
            await _consume_forever(client, session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("用量消费中断，%.1fs 后重连: %s", delay, exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY_S)


__all__ = ["MODEL_USAGE_CONSUMER_GROUP", "run_usage_consumer"]
