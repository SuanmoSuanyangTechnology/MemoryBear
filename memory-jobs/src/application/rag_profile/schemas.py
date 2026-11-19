"""Validate model output before persistence."""

from pydantic import BaseModel, Field


class RAGTag(BaseModel):
    tag: str = Field(min_length=1)
    frequency: int = Field(ge=1)


class RAGProfile(BaseModel):
    summary: str = Field(min_length=1)
    tags: list[RAGTag]
    personas: list[str]
    memory_insight: str = Field(min_length=1)
    behavior_pattern: str
    key_findings: str
    growth_trajectory: str


class RAGStatements(BaseModel):
    statement: str
    label: str | None = None


class RAGSummary(BaseModel):
    statements: list[RAGStatements] = Field(default_factory=list)
    summary: str | None = None


class RAGTags(BaseModel):
    tags: list[str]


class RAGPersonas(BaseModel):
    personas: list[str]


class RAGDomain(BaseModel):
    domain: str
