"""Host business-attribution context for usage events (UsageEvent.resource_type/resource_id).

Business entry points use the `@bind_usage(...)` decorator (or wrap with
`bind_usage_context(...)`); while active, all model call events automatically carry
that business attribution. Calls without a business context (background tasks, etc.)
set nothing, so event attribution stays null.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class UsageResourceContext:
    """Business attribution (agent/workflow/kb/memory...); the model domain does not interpret it, it only serves host-side aggregate stats."""

    resource_type: str
    resource_id: str | None = None


_usage_resource_var: ContextVar[UsageResourceContext | None] = ContextVar(
    "usage_resource_context", default=None
)


def get_usage_resource() -> UsageResourceContext | None:
    return _usage_resource_var.get()


@contextmanager
def bind_usage_context(
    resource_type: str, resource_id: str | UUID | None = None
) -> Iterator[None]:
    """Bind business attribution to the current context (nestable; inner scopes override; restored on exit)."""
    token = _usage_resource_var.set(
        UsageResourceContext(
            resource_type=resource_type,
            resource_id=None if resource_id is None else str(resource_id),
        )
    )
    try:
        yield
    finally:
        _usage_resource_var.reset(token)


def _resolve_usage_id(
    sig: inspect.Signature, args: tuple, kwargs: dict, id_path: str
) -> Any:
    parts = id_path.split(".")
    bound = sig.bind_partial(*args, **kwargs)
    value = bound.arguments.get(parts[0])
    for part in parts[1:]:
        value = getattr(value, part, None)
        if value is None:
            return None
    return value


def bind_usage(
    resource_type: str,
    id_path: str,
    *,
    only_if_unbound: bool = False,
) -> Callable:
    """Service entry decorator: resolves the business entity id from call arguments and binds usage attribution while inside.

    id_path is a dotted path: the first segment is the parameter name and the rest
    are attributes taken level by level from that object, e.g.
    ``"config.app_id"`` or ``"principal.current_workspace_id"``. When no id can be
    resolved (missing parameter / None value), nothing is bound and event
    attribution stays null.

    With only_if_unbound=True an existing outer business attribution is not
    overridden -- used by internal service funnels (RAG retrieval, memory writes)
    so the outer business context wins. Supports async functions and async
    generators.
    """
    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        def _resolve(args: tuple, kwargs: dict) -> tuple[bool, Any]:
            if only_if_unbound and get_usage_resource() is not None:
                return False, None
            try:
                resource_id = _resolve_usage_id(sig, args, kwargs, id_path)
            except Exception:  # noqa: BLE001 - attribution resolution is side-channel; no exception may affect the business entry point
                return False, None
            return resource_id is not None, resource_id

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                should_bind, resource_id = _resolve(args, kwargs)
                if not should_bind:
                    async for item in fn(*args, **kwargs):
                        yield item
                    return
                with bind_usage_context(resource_type, resource_id):
                    async for item in fn(*args, **kwargs):
                        yield item

            return wrapper

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                should_bind, resource_id = _resolve(args, kwargs)
                if not should_bind:
                    return await fn(*args, **kwargs)
                with bind_usage_context(resource_type, resource_id):
                    return await fn(*args, **kwargs)

            return wrapper

        raise TypeError(f"bind_usage 仅支持 async 函数或异步生成器: {fn!r}")

    return decorator
