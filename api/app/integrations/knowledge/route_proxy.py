"""Transparent knowledge route forwarding interface."""

from __future__ import annotations

import inspect
import uuid
from functools import wraps
from typing import Protocol

from fastapi import Request, Response

from .call_profile import CallProfile
from .contracts import (
    KnowledgeCallContext,
    KnowledgeContextError,
    KnowledgePrincipal,
    KnowledgeRetrievalSource,
)


class KnowledgeRouteProxy(Protocol):
    async def forward(
        self,
        request: Request,
        context: KnowledgeCallContext,
        *,
        profile: CallProfile = CallProfile.JSON,
    ) -> Response:
        raise NotImplementedError


def route_through_knowledge_service(
    *,
    source: KnowledgeRetrievalSource = KnowledgeRetrievalSource.MANAGER_API,
    profile: CallProfile = CallProfile.JSON,
    public: bool = False,
):
    """Use the remote route proxy when the process runtime enabled it."""

    def decorator(handler):
        @wraps(handler)
        async def wrapper(*args, **kwargs):
            from .runtime import get_knowledge_route_proxy

            proxy = get_knowledge_route_proxy()
            if proxy is None:
                result = handler(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result
            request = kwargs.get("request")
            if not isinstance(request, Request):
                request = next((item for item in args if isinstance(item, Request)), None)
            if request is None:
                raise KnowledgeContextError("Knowledge route request is required")
            principal = None
            if not public:
                user = kwargs.get("current_user")
                if user is None:
                    raise KnowledgeContextError("Authenticated knowledge user is required")
                principal = KnowledgePrincipal(
                    actor_id=uuid.UUID(str(user.id)),
                    actor_name=getattr(user, "username", None),
                    tenant_id=uuid.UUID(str(user.tenant_id)),
                    workspace_id=uuid.UUID(str(user.current_workspace_id)),
                )
            trace_id = getattr(request.state, "trace_id", "") or uuid.uuid4().hex
            context = KnowledgeCallContext(
                principal=principal,
                source=source,
                trace_id=trace_id,
            )
            return await proxy.forward(request, context, profile=profile)

        return wrapper

    return decorator
