from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.memory.storage.enums import MemoryNodeLabel

MAX_ATTEMPTS = 3  # 包含首次尝试；刻意不可配置。


class OutboxOperation(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"
    DRAFT_DELETE = "draft_delete"


class OutboxEventInput(BaseModel):
    """在主事务提交后构造一次；再次入队时复用这些 ID。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    label: MemoryNodeLabel
    node_id: str
    operation: OutboxOperation = OutboxOperation.UPSERT

    @field_validator("node_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("node_id must not be blank")
        return value  # 永不裁剪或改动业务身份标识。


@dataclass(frozen=True)
class ClaimedEvent:
    id: UUID
    sequence: int
    label: str
    node_id: str
    operation: str
    attempt_count: int
    claim_token: UUID
