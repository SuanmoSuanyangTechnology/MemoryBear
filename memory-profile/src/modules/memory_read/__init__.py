"""Memory-read module: the transport layer of the migrated read endpoints."""

from __future__ import annotations

from src.modules.memory_read.routes import router

__all__ = ["router"]