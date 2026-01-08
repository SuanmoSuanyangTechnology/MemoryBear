import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.memory_perceptual_model import PerceptualType, FileStorageType


class PerceptualFilter(BaseModel):
    type: PerceptualType | None = Field(
        default=None,
        description="Perceptual type used for filtering the query; optional"
    )


class PerceptualQuerySchema(BaseModel):
    filter: PerceptualFilter = Field(
        default_factory=lambda: PerceptualFilter(),
        description="Query filter containing perceptual type criteria"
    )

    page: int = Field(
        default=1,
        ge=1,
        description="Page number for pagination, starting from 1"
    )

    page_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of records per page, range 1-100"
    )


class PerceptualMemoryItem(BaseModel):
    """感知记忆项"""
    id: uuid.UUID = Field(..., description="Unique memory ID")
    perceptual_type: PerceptualType = Field(..., description="Type of perception, e.g., text, audio, or video")
    file_path: str = Field(..., description="File path in the storage service")
    file_ext: str = Field(..., description="File extension")
    file_name: str = Field(..., description="File name")
    summary: Optional[str] = Field(None, description="summary")
    storage_type: FileStorageType = Field(..., description="Storage type for file")
    created_time: int = Field(None, description="create time")

    class Config:
        from_attributes = True


class PerceptualTimelineResponse(BaseModel):
    """感知记忆时间线响应"""
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页大小")
    total_pages: int = Field(..., description="总页数")
    memories: list[PerceptualMemoryItem] = Field(..., description="记忆列表")

    class Config:
        from_attributes = True


# --------------------------
#  TODO: FileMetaData
# --------------------------
class Identity(BaseModel):
    title: str
    filename: str
    source: str  # upload | crawl | system
    author: Optional[str] = None


class Semantic(BaseModel):
    topic: str
    domain: str
    difficulty: str  # beginner | intermediate | advanced
    intent: str  # informative | instructional | promotional
    sentiment: str  # positive | neutral | negative


class Content(BaseModel):
    summary: str
    keywords: list[str]
    topic: str
    domain: str


class Usage(BaseModel):
    target_audience: list[str]
    use_cases: list[str]


class Stats(BaseModel):
    duration_sec: Optional[int] = None
    char_count: int
    word_count: int


class Processing(BaseModel):
    transcribed: bool
    ocr_applied: bool
    chunked: bool
    vectorized: bool
    embedding_model: Optional[str] = None


class VideoModal(BaseModel):
    scene: list[str]


class AudioModal(BaseModel):
    speaker_count: int


class TextModal(BaseModel):
    section_count: int
    title: str
    first_line: str


class Asset(BaseModel):
    type: str
    modality: str  # text | audio | video
    format: str  # docx | mp3 | mp4
    language: str
    encoding: str

    identity: Identity
    semantic: Semantic
    content: Content
    usage: Usage
    stats: Stats
    processing: Processing
    created_at: str
    modalities: AudioModal | TextModal | VideoModal
