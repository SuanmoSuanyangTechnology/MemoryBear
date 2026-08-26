"""Transparent knowledge route forwarding interface."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request, Response

from .call_profile import CallProfile
from .contracts import KnowledgeCallContext


class KnowledgeRouteProxy(Protocol):
    async def forward(
        self,
        request: Request,
        context: KnowledgeCallContext,
        *,
        profile: CallProfile,
    ) -> Response:
        raise NotImplementedError
