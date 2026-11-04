"""Public display records; nullable fields remain required response keys."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class WrittenItem(BaseModel):
    id: str
    end_user_id: str
    memory_id: str | None
    memory_type: str | None
    name: str | None
    content: str
    occurred_at: int | None


class RetrievedItem(BaseModel):
    id: str
    end_user_id: str
    search_mode: str | None
    query: str
    content: str
    occurred_at: int | None


class EngineItem(BaseModel):
    id: str
    end_user_id: str | None
    engine_type: str
    name: str
    content: str
    occurred_at: int


class AllWrittenItem(WrittenItem):
    source: Literal["written"]


class AllRetrievedItem(RetrievedItem):
    source: Literal["retrieved"]


class AllEngineItem(EngineItem):
    source: Literal["engines"]


AllItem = Annotated[
    AllWrittenItem | AllRetrievedItem | AllEngineItem, Field(discriminator="source")
]
