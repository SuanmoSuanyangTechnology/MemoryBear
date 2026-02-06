"""Data models for Feishu integration."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional


@dataclass
class FileInfo:
    """File information from Feishu."""
    token: str
    name: str
    type: str  # doc/docx/sheet/bitable/file/slides/folder
    created_time: datetime
    modified_time: datetime
    owner_id: str
    url: str
