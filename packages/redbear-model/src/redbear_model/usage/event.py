"""用量事件契约：对齐主 spec §13.1 冻结 JSON schema（事件入 Stream，消费落 model_usage_records）。

capability 值词汇依 `2026-09-09-model-contract-v2-design.md` §1#3/§3 = 归一化 ModelType
接口族值（llm/embedding/rerank/image/video/asr）——`"chat"` 输入由 `ModelType._missing_`
读侧归一为 llm，事件写值永不落 "chat"，持久契约零迁移。
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from ..contracts import ContractModel, ModelProvider, ModelType


class UsageStatus(StrEnum):
    """spec §13.1：单次调用终态。"""

    OK = "ok"
    FALLBACK_SUCCEEDED = "fallback_succeeded"  # 换渠道后成功
    FAILED = "failed"


class UsageEvent(ContractModel):
    """单次模型调用用量事件（frozen；host 构造后经 publisher 旁路出口）。"""

    event_id: UUID = Field(default_factory=uuid.uuid4)
    ts_ms: int = Field(default_factory=lambda: int(time.time() * 1000), ge=0)
    source_service: str = Field(min_length=1)  # 微服务化后标识来源宿主
    tenant_id: UUID
    config_id: UUID  # 调用入口 config；config 删行后用量仍留档
    channel_id: UUID | None = None
    provider: ModelProvider
    model_name: str = Field(min_length=1)
    capability: ModelType  # 接口族/调用形态（09-09 决策归一词汇，见模块 docstring）
    stream: bool = False
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    images_count: int | None = Field(default=None, ge=1)  # 多模态复用 capability + 图数
    latency_ms: int = Field(default=0, ge=0)
    status: UsageStatus = UsageStatus.OK
    error_type: str | None = None
    attempts: int = Field(default=1, ge=1)  # 含换渠道总尝试次数
    request_id: str | None = None  # 宿主链路 id
    resource_type: str | None = None  # 宿主业务归因（agent/workflow/kb/...），模型域不解释
    resource_id: UUID | None = None

    @model_validator(mode="after")
    def normalize_error_type(self):
        # failed 事件必须带 error_type（消费端按 error_type 聚合失败面）
        if self.status is UsageStatus.FAILED and not self.error_type:
            object.__setattr__(self, "error_type", "unknown")
        return self

    def to_stream_dict(self) -> dict[str, object]:
        """§13.1 键的 flat dict：枚举→value、UUID→str、None 保留，宿主 JSON 序列化后入 Stream。"""
        return {
            "event_id": str(self.event_id),
            "ts_ms": self.ts_ms,
            "source_service": self.source_service,
            "tenant_id": str(self.tenant_id),
            "config_id": str(self.config_id),
            "channel_id": None if self.channel_id is None else str(self.channel_id),
            "provider": self.provider.value,
            "model_name": self.model_name,
            "capability": self.capability.value,
            "stream": self.stream,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "images_count": self.images_count,
            "latency_ms": self.latency_ms,
            "status": self.status.value,
            "error_type": self.error_type,
            "attempts": self.attempts,
            "request_id": self.request_id,
            "resource_type": self.resource_type,
            "resource_id": None if self.resource_id is None else str(self.resource_id),
        }
