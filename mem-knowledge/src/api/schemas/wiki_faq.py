"""FAQ 管理只开放标准问与答案首项，批量操作使用显式 ID。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_FAQ_BATCH_ITEMS = 100


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


class FAQApprove(FAQExpected):
    content_revision: int = Field(ge=1)
    reviewed_wiki_snapshot_id: UUID
    idempotency_key: UUID


class FAQBatchExpected(FAQExpected):
    faq_id: UUID


class FAQBatchApproval(FAQApprove):
    faq_id: UUID
    # 混合选择中已通过、无来源的手工 FAQ 无需审核；其余项仍须提交真实快照。
    reviewed_wiki_snapshot_id: UUID | None = None


class FAQBatchContext(FAQRequest):
    faq_ids: list[UUID] = Field(min_length=1, max_length=MAX_FAQ_BATCH_ITEMS)

    @field_validator("faq_ids")
    @classmethod
    def distinct_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class FAQBatchApprove(FAQRequest):
    items: list[FAQBatchApproval] = Field(min_length=1, max_length=MAX_FAQ_BATCH_ITEMS)

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
