"""Shared logging and request-context primitives."""

from src.common.observability.context import TRACE_ID_HEADER, get_trace_id, normalize_trace_id
from src.common.observability.logging import get_logger, setup_logging

__all__ = [
    "TRACE_ID_HEADER",
    "get_logger",
    "get_trace_id",
    "normalize_trace_id",
    "setup_logging",
]
