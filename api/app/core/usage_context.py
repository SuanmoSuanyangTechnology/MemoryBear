"""用量事件的宿主业务归因上下文（UsageEvent.resource_type/resource_id）。

业务入口用 `@bind_usage(...)` 装饰器（或 `bind_usage_context(...)` 包一层），
期间所有模型调用事件自动携带该业务归因；无业务上下文的调用（后台任务等）
不设置，事件归因为 null。
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
    """业务归因（agent/workflow/kb/memory...）；模型域不解释，仅供宿主侧聚合统计。"""

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
    """绑定当前上下文的业务归因（可嵌套，内层覆盖，退出自动还原）。"""
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
    """服务入口装饰器：从调用参数解析业务实体 id，进入期间绑定用量归因。

    id_path 为点分路径：首段是参数名，其余段在该对象上逐级取属性，例如
    ``"config.app_id"``、``"principal.current_workspace_id"``。解析不到 id
    （参数缺失 / 值为 None）时不绑定，事件归因保持 null。

    only_if_unbound=True 时外层已有业务归因则不覆盖——服务内部漏斗
    （RAG 检索、记忆写入）用，外层业务上下文优先。支持 async 函数与
    异步生成器。
    """
    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        def _resolve(args: tuple, kwargs: dict) -> tuple[bool, Any]:
            if only_if_unbound and get_usage_resource() is not None:
                return False, None
            try:
                resource_id = _resolve_usage_id(sig, args, kwargs, id_path)
            except Exception:  # noqa: BLE001 - 归因解析旁路，任何异常不影响业务入口
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
