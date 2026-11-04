"""Shared perceptual memory enums and error messages."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, RootModel


class PerceptualType(IntEnum):
    VISION = 1
    AUDIO = 2
    TEXT = 3
    CONVERSATION = 4


class FileStorageService(IntEnum):
    LOCAL = 1
    REMOTE = 2


class PerceptualCounts(BaseModel):
    vision: int
    audio: int
    text: int
    conversation: int


class MemoryCount(BaseModel):
    total: int
    by_type: PerceptualCounts


class TimelineItem(BaseModel):
    id: str
    perceptual_type: PerceptualType
    storage_service: FileStorageService
    file_path: str
    file_name: str
    file_ext: str
    summary: str | None
    meta_data: Any
    topic: Any
    domain: Any
    keywords: Any
    created_time: int | None


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
    keywords: list[Any]
    topic: str
    domain: str
    created_time: int | None


class VisualMemory(LatestMemoryBase):
    scene: list[Any]


class AudioMemory(LatestMemoryBase):
    speaker_count: int


class TextMemory(LatestMemoryBase):
    section_count: Any
    title: Any
    first_line: Any


class EmptyMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LatestMemory(RootModel[VisualMemory | AudioMemory | TextMemory | EmptyMemory]):
    pass
