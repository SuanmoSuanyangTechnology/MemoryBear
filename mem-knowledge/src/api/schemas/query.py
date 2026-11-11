"""HTTP query constraints shared by the knowledge list endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator

from ...models.owned import Document, File, Knowledge, KnowledgeShare

# Preserve every sortable column, excluding relationships and ORM methods.
KnowledgeOrder = Literal["", *tuple(Knowledge.__table__.columns.keys())]
DocumentOrder = Literal["", *tuple(Document.__table__.columns.keys())]
FileOrder = Literal["", *tuple(File.__table__.columns.keys())]
ShareOrder = Literal["", *tuple(KnowledgeShare.__table__.columns.keys())]


def csv_uuids(value: str) -> str:
    if not value:
        return value
    values = []
    for index, part in enumerate(value.split(",")):
        try:
            values.append(str(UUID(part.strip())))
        except ValueError as exc:
            raise ValueError(f"第 {index + 1} 项必须是有效 UUID") from exc
    return ",".join(values)


def wiki_types(value: str) -> str:
    values = {part.strip() for part in value.split(",") if part.strip()}
    if values - {"summary", "entity", "concept"}:
        raise ValueError("页面类型只能是 summary、entity 或 concept")
    return value


CsvUUIDs = Annotated[str, AfterValidator(csv_uuids)]
WikiPageTypes = Annotated[str, AfterValidator(wiki_types)]
