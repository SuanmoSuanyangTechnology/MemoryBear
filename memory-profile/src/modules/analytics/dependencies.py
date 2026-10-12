"""Authenticated Memory request context for analytics handlers."""

from __future__ import annotations

from fastapi import HTTPException, Request

from src.middleware.auth import MemoryPrincipal

MemoryRequestContext = MemoryPrincipal


async def require_memory_context(request: Request) -> MemoryRequestContext:
    context = getattr(request.state, "memory_context", None)
    if not isinstance(context, MemoryPrincipal):
        raise HTTPException(status_code=401, detail="missing authenticated principal")
    return context
