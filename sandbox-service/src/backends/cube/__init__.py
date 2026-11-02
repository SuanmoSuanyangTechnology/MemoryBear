"""Cube backend package. ``build`` is the entry point the factory calls."""

from __future__ import annotations

from collections.abc import Mapping

from ...clock import Clock
from .backend import CubeBackend
from .config import CubeConfig, load_config

__all__ = ["CubeBackend", "CubeConfig", "build", "load_config"]


def build(env: Mapping[str, str], clock: Clock) -> CubeBackend:
    return CubeBackend(load_config(env), clock)
