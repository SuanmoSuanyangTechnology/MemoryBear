"""FAQ 管理只开放标准问与答案首项，批量操作使用显式 ID。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_FAQ_BATCH_ITEMS = 100
LEGACY_APPROVAL_FIELDS = frozenset(
    {"expected_row_version", "content_revision", "reviewed_wiki_snapshot_id", "idempotency_key"}
)


class FAQRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FAQCreate(FAQRequest):
    standard_question: str = Field(min_length=1, max_length=200)
    standard_answer: str = Field(min_length=1)

    @field_validator("standard_question", "standard_answer")
    @classmethod
    def strip_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("标准问和标准答不能为空")
        return value.strip()


class FAQEdit(FAQCreate):
    expected_row_version: int = Field(ge=1)


class FAQExpected(FAQRequest):
    expected_row_version: int = Field(ge=1)


class FAQEnabled(FAQExpected):
    is_enabled: bool


class FAQApprove(FAQRequest):
    """审核执行时的当前内容；旧版本与快照参数兼容接收但忽略。"""

    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_review_target(cls, value: Any) -> Any:
        # 单条和批量均由服务端选择当前基线，旧字段不做类型或版本校验。
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key not in LEGACY_APPROVAL_FIELDS}
        return value


class FAQBatchExpected(FAQExpected):
    faq_id: UUID


class FAQBatchApproval(FAQApprove):
    """批量审核以执行时的 FAQ 内容和最新已发布 Wiki 快照为准。"""

    faq_id: UUID = Field(description="待审核 FAQ ID；由服务端在事务内读取最新状态")


class FAQBatchContext(FAQRequest):
    faq_ids: list[UUID] = Field(min_length=1, max_length=MAX_FAQ_BATCH_ITEMS)

    @field_validator("faq_ids")
    @classmethod
    def distinct_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class FAQBatchApprove(FAQRequest):
    items: list[FAQBatchApproval] = Field(
        min_length=1,
        max_length=MAX_FAQ_BATCH_ITEMS,
        description="按 FAQ ID 去重；审核当前内容并推进至最新已发布 Wiki 基线，已达目标则跳过",
    )

    @model_validator(mode="after")
    def deduplicate(self):
        self.items = _unique_items(self.items)
        return self


class FAQBatchEnabled(FAQRequest):
    items: list[FAQBatchExpected] = Field(min_length=1, max_length=MAX_FAQ_BATCH_ITEMS)
    is_enabled: bool

    @model_validator(mode="after")
    def deduplicate(self):
        self.items = _unique_items(self.items)
        return self


def _unique_items(items):
    unique = {}
    for item in items:
        if item.faq_id in unique and unique[item.faq_id] != item:
            raise ValueError("同一 FAQ 的重复项必须使用相同的并发版本与审核目标")
        unique[item.faq_id] = item
    return list(unique.values())
