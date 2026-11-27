"""Aggregate entry point for the file-service model tables."""

from .base import ServiceBase
from .file_metadata_model import FileMetadata
from .generic_file_model import GenericFile

__all__ = [
    "FileMetadata",
    "GenericFile",
    "ServiceBase",
]
