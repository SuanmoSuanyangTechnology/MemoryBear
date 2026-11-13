"""Wire models for explicit-memory endpoints (T07–T10).

Raw graph properties use Any deliberately: the legacy response has data: Any
and passes through truthy values (including historical arrays/numbers). Adding
string validation here would change existing responses into business errors.
Only computed fields and request bodies retain their existing constraints.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class OverviewRequest(BaseModel):
    """T07 body; language_type remains accepted but unconsumed."""

    end_user_id: str = Field(..., description="终端用户ID")
    language_type: str | None = Field("zh", description="语言类型（zh/en）")


class DetailsRequest(BaseModel):
    end_user_id: str = Field(..., description="终端用户ID")
    memory_id: str = Field(..., description="记忆ID（情景记忆或语义记忆的ID）")
    language_type: str | None = Field("zh", description="语言类型（zh/en）")


class EpisodicOverviewItem(BaseModel):
    id: str
    title: Any
    content: Any
    created_at: int | None = None


class SemanticMemoryItem(BaseModel):
    id: str
    name: Any
    entity_type: Any
    core_definition: Any


class OverviewData(BaseModel):
    total: int
    episodic_memories: list[EpisodicOverviewItem]
    semantic_memories: list[SemanticMemoryItem]


class EpisodicPageInfo(BaseModel):
    page: int
    pagesize: int
    total: int
    hasnext: bool


class EpisodicListItem(BaseModel):
    id: str
    title: Any
    memory_type: Any
    content: Any
    created_at: int | None = None


class EpisodicListData(BaseModel):
    # Unfiltered total; page.total is the filtered total, as in the monolith.
    total: int
    items: list[EpisodicListItem]
    page: EpisodicPageInfo


class EpisodicDetails(BaseModel):
    memory_type: Literal["episodic"]
    title: Any
    content: Any
    emotion: Any = None
    created_at: int | None = None


class SemanticDetails(BaseModel):
    memory_type: Literal["semantic"]
    name: Any
    core_definition: Any
    detailed_notes: Any
    created_at: int | None = None


ExplicitDetailsData = Annotated[
    EpisodicDetails | SemanticDetails, Field(discriminator="memory_type")
]
