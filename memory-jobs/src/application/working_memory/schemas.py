"""Validated conversation summary output."""

from pydantic import BaseModel


class ConversationDetailData(BaseModel):
    theme: str
    question: list[str]
    summary: str
    takeaways: list[str]
    info_score: int
