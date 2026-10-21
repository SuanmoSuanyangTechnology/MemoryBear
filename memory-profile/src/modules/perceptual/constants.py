"""Shared perceptual memory enums and error messages."""

from __future__ import annotations

from enum import IntEnum


class PerceptualType(IntEnum):
    VISION = 1
    AUDIO = 2
    TEXT = 3
    CONVERSATION = 4


class FileStorageService(IntEnum):
    LOCAL = 1
    REMOTE = 2
