"""宿主计量出口：包 UsagePublisher 协议的 Redis Stream 实现（spec §13.2）。

XADD `model:usage`（单 payload 字段 = 事件 JSON 文本），XTRIM 限长 500k；
publisher 失败仅告警（`publish_usage_safely` 兜底），绝不阻塞/回滚业务调用。
"""

from __future__ import annotations

import json

from redbear_model import UsageEvent, UsagePublisher

from ..infrastructure.redis import get_sync_client

MODEL_USAGE_STREAM = "model:usage"
MODEL_USAGE_STREAM_MAXLEN = 500_000


class RedisStreamPublisher(UsagePublisher):
    """用量事件 → `model:usage` Stream（消费组 `model-usage-consumers` 由消费端建立）。"""

    def report_usage(self, event: UsageEvent) -> None:
        get_sync_client().xadd(
            MODEL_USAGE_STREAM,
            {"payload": json.dumps(event.to_stream_dict(), ensure_ascii=False)},
            maxlen=MODEL_USAGE_STREAM_MAXLEN,
            approximate=True,
        )


usage_publisher = RedisStreamPublisher()
