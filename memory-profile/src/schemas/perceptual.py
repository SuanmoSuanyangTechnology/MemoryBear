"""Perceptual fields and coercion rules matching the original API."""

from enum import IntEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, RootModel


class PerceptualType(IntEnum):
    VISION = 1
    AUDIO = 2
    TEXT = 3
    CONVERSATION = 4


class FileStorageService(IntEnum):
    LOCAL = 1
    REMOTE = 2


class PerceptualContent(BaseModel):
    keywords: list[str]
    topic: str
    domain: str


class VisualModal(BaseModel):
    scene: list[str]


class AudioModal(BaseModel):
    speaker_count: int


class TextModal(BaseModel):
    section_count: int
    title: str
    first_line: str


class PerceptualCounts(BaseModel):
    vision: int
    audio: int
    text: int
    conversation: int


class MemoryCount(BaseModel):
    total: int
    by_type: PerceptualCounts


class TimelineItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    perceptual_type: PerceptualType
    storage_service: FileStorageService
    file_path: str
    file_name: str
    file_ext: str
    summary: str | None = None
    meta_data: dict[str, Any] | None = None
    topic: str
    domain: str
    keywords: list[str]
    created_time: int


class MemoryTimeline(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    memories: list[TimelineItem]


class LatestMemoryBase(BaseModel):
    id: str
    file_name: str
    file_path: str
    storage_type: int | None
    summary: str | None
    keywords: list[str]
    topic: str
    domain: str
    created_time: int | None


class VisualMemory(LatestMemoryBase, VisualModal):
    pass


class AudioMemory(LatestMemoryBase, AudioModal):
    pass


class TextMemory(LatestMemoryBase, TextModal):
    pass


class EmptyMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LatestMemory(RootModel[VisualMemory | AudioMemory | TextMemory | EmptyMemory]):
    pass
