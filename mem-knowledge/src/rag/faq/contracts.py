"""标准问及其问法共用答案集合；首项编辑不能丢失隐藏字段。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FAQ_GENERATE_TASK = "mem_knowledge.tasks.generate_wiki_faq"
FAQ_QUEUE = "faq_tasks"
FAQ_PROMPT_VERSION = "wiki_faq_v1"


class FAQContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard_question: str = Field(min_length=1)
    similar_questions: list[str] = Field(default_factory=list)
    negative_questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(min_length=1)
    answer_strategy: Literal["all", "random"] = "all"

    @model_validator(mode="after")
    def check_relationships(self):
        # 只校验，不排序或重写当前客户端不可见的字段。
        if not self.standard_question.strip() or any(not a.strip() for a in self.answers):
            raise ValueError("标准问和答案不能为空")
        positive = [self.standard_question, *self.similar_questions]
        positive = [q.strip().casefold() for q in positive]
        negative = [q.strip().casefold() for q in self.negative_questions]
        if (
            any(not q for q in positive + negative)
            or len(set(positive)) != len(positive)
            or len(set(negative)) != len(negative)
            or set(positive).intersection(negative)
        ):
            raise ValueError("标准问、相似问或反例存在重复/冲突")
        return self


class GeneratedFAQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    standard_question: str = Field(min_length=1, max_length=200)
    standard_answer: str = Field(min_length=1)

    @field_validator("standard_question", "standard_answer")
    @classmethod
    def strip_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("标准问和标准答不能为空")
        return value.strip()


class GeneratedFAQs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[GeneratedFAQ]


def edit_content(current: FAQContent, question: str, answer: str) -> FAQContent:
    """只替换两个开放字段，校验合并结果并保留答案顺序。"""
    return FAQContent.model_validate(
        {
            **current.model_dump(),
            "standard_question": question,
            "answers": [answer, *current.answers[1:]],
        }
    )
