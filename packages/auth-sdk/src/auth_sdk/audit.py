"""审计写入：Redis Stream 入队（网关无 DB 层，落库由身份与计费消费者完成）；失败/超时静默不阻塞业务。

入队即生成 event_id（幂等键）——消费者 XREADGROUP 批量插 audit_logs 时
ON CONFLICT (event_id) DO NOTHING，多副本/崩溃重投不产生重复行。
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone

from redis.exceptions import RedisError

from auth_sdk.schema import AuditEvent


class AuditLogger:
    def __init__(self, redis, stream_key: str = "audit:stream", timeout_ms: int = 100):
        self._redis = redis
        self._stream_key = stream_key
        self._timeout_s = timeout_ms / 1000

    async def audit(self, event: AuditEvent) -> None:
        event_id = event.event_id or uuid.uuid4().hex
        payload = {
            "event_id": event_id,
            "event_type": event.event_type, "actor_id": event.actor_id,
            "tenant_id": event.tenant_id, "target": event.target,
            "result": event.result, "detail": event.detail,
            "ts": (event.ts or datetime.now(timezone.utc)).isoformat(),
        }
        try:
            # 超时保护：Redis 半开/慢时 xadd 最多等 timeout_ms，绝不阻塞业务请求
            await asyncio.wait_for(
                self._redis.xadd(self._stream_key, payload),
                timeout=self._timeout_s)
        except (TimeoutError, RedisError):
            pass  # 审计失败/超时不阻塞业务（后续增强：本地缓冲重试）
