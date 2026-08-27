"""Data models for web crawler."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CrawledDocument:
    """Represents a successfully processed web page with extracted content."""

    url: str
    title: str
    content: str
    content_length: int
    crawl_timestamp: datetime
    http_status: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    """Represents the result of an HTTP fetch operation."""

    url: str
    final_url: str
    status_code: int
    content: str | None
    headers: dict[str, str]
    error: str | None
    success: bool


@dataclass
class ExtractedContent:
    """Represents content extracted from HTML."""

    title: str
    text: str
    is_static: bool
    word_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrawlSummary:
    """Represents statistics from a completed crawl."""

    total_pages_processed: int
    total_errors: int
    total_skipped: int
    total_urls_discovered: int
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    error_breakdown: dict[str, int] = field(default_factory=dict)
