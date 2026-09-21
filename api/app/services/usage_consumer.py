"""用量事件消费（spec §13.2）：XREADGROUP 批量拉取 → 批量落表 → XACK。

- 消费组 `model-usage-consumers`（消费端建立，`MKSTREAM` + `$` 起点：只消费建组后的新事件）
- 每轮预算：批量 200 × 最多 20 批 / 10s 时间窗，读到空立即返回（beat 周期触发）
- XAUTOCLAIM 兜底：pending idle > 60s 的消息（消费进程崩溃/落表失败遗留）重新领取处理
- 幂等：`ON CONFLICT (event_id) DO NOTHING`，重复投递不重复落表、照常 ACK
- 坏消息（payload 缺失/JSON 非法/字段类型错）记 warning 后 ACK，避免毒丸阻塞消费组
- 落表失败（DB 短暂不可用）不 ACK：消息留在 pending 由下一轮 XAUTOCLAIM 重领
- 可观测：XLEN / XPENDING 超阈值告警日志；运行摘要返回 Celery 结果

旁路铁律：本模块只读 stream 与写 model_usage_records，不影响业务调用链路。
"""
from __future__ import annotations

import json
import os
import socket
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError, ResponseError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.logging_config import get_logger
from app.db import get_db_context
from app.models.model_usage_record import ModelUsageRecord
from app.usage_publisher import MODEL_USAGE_STREAM

logger = get_logger(__name__)

MODEL_USAGE_CONSUMER_GROUP = "model-usage-consumers"

_BATCH_SIZE = 200
_MAX_BATCHES_PER_RUN = 20
_TIME_BUDGET_SECONDS = 10.0
_RECLAIM_MIN_IDLE_MS = 60_000
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


def _consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def _ensure_group(client) -> None:
    try:
        client.xgroup_create(MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP, id="$", mkstream=True)
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
        raise ValueError("payload is not a JSON object")  # noqa: TRY004 - 坏载荷统一 ValueError，调用方据此 ACK
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
        "channel_id": None if data.get("channel_id") is None else _to_uuid(data["channel_id"], "channel_id"),
        "resource_type": None if data.get("resource_type") is None else str(data["resource_type"])[:32],
        "resource_id": None if data.get("resource_id") is None else _to_uuid(data["resource_id"], "resource_id"),
        "provider": str(data["provider"])[:50],
        "model_name": str(data["model_name"])[:255],
        "capability": capability,
        "stream": bool(data.get("stream", False)),
        "input_tokens": 0 if data.get("input_tokens") is None else _to_int(data["input_tokens"], "input_tokens"),
        "output_tokens": 0 if data.get("output_tokens") is None else _to_int(data["output_tokens"], "output_tokens"),
        "images_count": _optional_int(data.get("images_count"), "images_count"),
        "latency_ms": 0 if data.get("latency_ms") is None else _to_int(data["latency_ms"], "latency_ms"),
        "status": status,
        "error_type": None if data.get("error_type") is None else str(data["error_type"])[:64],
        "attempts": max(1, 1 if data.get("attempts") is None else _to_int(data["attempts"], "attempts")),
        "request_id": None if data.get("request_id") is None else str(data["request_id"])[:64],
        "created_at": _to_datetime(data["ts_ms"], "ts_ms"),
    }


def _insert_rows(rows: list[dict[str, Any]]) -> int:
    """批量幂等落表，返回实际新插入行数（重复 event_id 跳过）。"""
    if not rows:
        return 0
    with get_db_context() as db:
        statement = (
            pg_insert(ModelUsageRecord)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(ModelUsageRecord.event_id)
        )
        inserted = db.execute(statement).fetchall()
        db.commit()
    return len(inserted)


def _handle_entries(
    entries: list[tuple[str, dict[str, str]]], stats: dict[str, int]
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
    stats["inserted"] += _insert_rows(rows)
    return ack_ids


def _read_new(client, batch_size: int) -> list[tuple[str, dict[str, str]]]:
    response = client.xreadgroup(
        MODEL_USAGE_CONSUMER_GROUP,
        _consumer_name(),
        {MODEL_USAGE_STREAM: ">"},
        count=batch_size,
    )
    entries: list[tuple[str, dict[str, str]]] = []
    for _stream, messages in response or []:
        entries.extend(messages)
    return entries


def _reclaim_stale(client, stats: dict[str, int]) -> None:
    """XAUTOCLAIM 兜底：领取 idle 超阈的 pending 消息（上一轮落表失败/进程崩溃遗留）。"""
    cursor = "0-0"
    while True:
        try:
            claimed = client.xautoclaim(
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
            ack_ids = _handle_entries(messages, stats)
            if ack_ids:
                client.xack(MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP, *ack_ids)
        if not messages or cursor in ("0-0", "0", None):
            return


def _warn_on_backlog(client) -> None:
    threshold = settings.MODEL_USAGE_BACKLOG_WARN
    try:
        length = client.xlen(MODEL_USAGE_STREAM)
    except RedisError as exc:
        logger.warning("用量 stream 长度查询失败: %s", exc)
        return
    if length and length > threshold:
        logger.warning("用量 stream 积压: xlen=%d 超阈值 %d", length, threshold)
    try:
        pending = client.xpending(MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP)
    except ResponseError:
        return
    pending_count = pending.get("pending") if isinstance(pending, dict) else None
    if pending_count and pending_count > threshold:
        logger.warning("用量消费组 pending 积压: %d 超阈值 %d", pending_count, threshold)


def consume_model_usage(
    *,
    batch_size: int = _BATCH_SIZE,
    max_batches: int = _MAX_BATCHES_PER_RUN,
    time_budget_seconds: float = _TIME_BUDGET_SECONDS,
) -> dict[str, Any]:
    """单轮消费：先领遗留 pending，再按预算读新消息；返回运行摘要。"""
    from app.aioRedis import get_thread_safe_sync_redis

    started = time.monotonic()
    deadline = started + time_budget_seconds
    stats = {"read": 0, "inserted": 0, "malformed": 0, "reclaimed": 0, "batches": 0}
    client = get_thread_safe_sync_redis()
    _ensure_group(client)
    _warn_on_backlog(client)
    _reclaim_stale(client, stats)

    while stats["batches"] < max_batches and time.monotonic() < deadline:
        entries = _read_new(client, batch_size)
        stats["batches"] += 1
        if not entries:
            break
        ack_ids = _handle_entries(entries, stats)
        if ack_ids:
            client.xack(MODEL_USAGE_STREAM, MODEL_USAGE_CONSUMER_GROUP, *ack_ids)

    stats["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if stats["read"] or stats["reclaimed"]:
        logger.info(
            "用量消费完成: read=%d inserted=%d malformed=%d reclaimed=%d elapsed_ms=%d",
            stats["read"],
            stats["inserted"],
            stats["malformed"],
            stats["reclaimed"],
            stats["elapsed_ms"],
        )
    stats["status"] = "SUCCESS"
    return stats
