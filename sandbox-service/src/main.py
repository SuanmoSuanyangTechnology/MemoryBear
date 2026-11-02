"""Process entry point.

Run with ``uvicorn src.main:app`` (same as the other services in this repository).
The composition root lives in :mod:`src.bootstrap`; importing this module builds the
app once, so configuration errors (missing backend, missing token, bad numbers,
unusable egress policy) surface at start-up as a clear failure.
"""

from __future__ import annotations

from .bootstrap import create_app_from_env

__all__ = ["app", "create_app_from_env"]

app = create_app_from_env()
