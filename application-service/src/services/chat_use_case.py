"""The /v1 chat use case shared by the HTTP route and the gRPC servicer.

Both transports run exactly the same resolution and execution legs; only the
frame rendering differs (SSE strings vs ChatFrame). Keeping this module free
of HTTP request/response objects is what makes the gRPC servicer a thin
adapter instead of a second copy of the chat pipeline.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_async_db_context
from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models import App, AppRelease, AppShare, AppType
from ..repositories import cross_domain_read
from ..response_utils import success
from ..schemas.app_schema import AppChatRequest
from ..schemas.conversation_schema import ChatResponse
from ..utils.app_config_utils import (
    agent_config_4_app_release,
    multi_agent_config_4_app_release,
    workflow_config_4_app_release,
)
from ..utils.redis_cache import CACHE_MISS, get_json_async, set_json_async
from ..utils.sse_stream import with_sse_keepalive
from .app_chat_service import AppChatService
from .conversation_service import ConversationService
from .public_chat_context import (
    _get_or_create_public_end_user_async,
    _prepare_public_memory_context_async,
)


@dataclass(frozen=True)
class ChatPreparation:
    """Everything the stream/complete legs need, resolved once per request."""

    app_id: UUID
    app_type: Any
    workspace_id: UUID
    end_user_id: str
    conversation_id: UUID | None
    payload: AppChatRequest
    runtime_config: Any
    release_id: UUID
    storage_type: str
    user_rag_memory_id: str


def require_bound_app_id(app_id: UUID | None) -> UUID:
    """Host ``_get_app_id``: reject keys that are not bound to an application."""

    if not app_id:
        raise BusinessException("API Key 未绑定应用", BizCode.BAD_REQUEST)
    return app_id


async def _get_v1_chat_app_async(
    db: AsyncSession, app_id: UUID, workspace_id: UUID
) -> App:
    """The active app row plus the workspace accessibility check (own
    workspace or an active share into it)."""

    app = await db.scalar(
        select(App).where(App.id == app_id, App.is_active.is_(True)).limit(1)
    )
    if not app:
        raise ResourceNotFoundException("应用", str(app_id))
    if app.workspace_id != workspace_id:
        share_exists = await db.scalar(
            select(AppShare.id).where(
                AppShare.source_app_id == app.id,
                AppShare.target_workspace_id == workspace_id,
                AppShare.is_active.is_(True),
            ).limit(1)
        )
        if not share_exists:
            raise BusinessException("应用不可访问", BizCode.WORKSPACE_NO_ACCESS)
    return app


async def _get_release_by_id_async(
    db: AsyncSession, app_id: UUID, release_id: UUID
) -> AppRelease:
    """Active release snapshot by id."""

    release = await db.scalar(
        select(AppRelease).where(
            AppRelease.app_id == app_id,
            AppRelease.id == release_id,
            AppRelease.is_active.is_(True),
        ).limit(1)
    )
    if not release:
        raise BusinessException(
            f"版本 {release_id} 不存在或已下线", BizCode.RELEASE_NOT_FOUND
        )
    return release


async def _get_current_release_async(
    db: AsyncSession, *, app_id: UUID, workspace_id: UUID
) -> AppRelease | None:
    """Re-validate the app (a cache-hit stub carries no release pointer) and
    return None when the app is not published."""

    app = await _get_v1_chat_app_async(db, app_id, workspace_id)
    if not app.current_release_id:
        return None
    return await db.get(AppRelease, app.current_release_id)


def _check_app_config(release: AppRelease) -> None:
    """The release snapshot must carry usable config (host ``_checkAppConfig``)."""

    if release.type == AppType.AGENT:
        if not release.config:
            raise BusinessException("Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    elif release.type == AppType.MULTI_AGENT:
        if not release.config:
            raise BusinessException("Multi-Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    elif release.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        if not release.config:
            raise BusinessException("工作流应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
    else:
        raise BusinessException("不支持的应用类型", BizCode.APP_TYPE_NOT_SUPPORTED)


async def prepare_chat(
    *, app_id: UUID, workspace_id: UUID, payload: AppChatRequest
) -> ChatPreparation:
    """Resolve the app, release, end user, memory context and conversation.

    Every validation finishes before a streaming leg opens, so transport-level
    failures still surface as pre-stream errors.
    """

    # App cache: a hit skips the app row lookup; the cached current release id
    # skips the release-by-current lookup too (host cache shape, 60s TTL).
    app_cache_key = f"cache:v2:app:{app_id}"
    app_cache = await get_json_async(app_cache_key)
    app_stub = None
    cached_release_id = None
    if app_cache is not CACHE_MISS:
        app_stub = SimpleNamespace(id=UUID(app_cache["id"]), type=app_cache["type"])
        if app_cache.get("current_release_id"):
            cached_release_id = UUID(app_cache["current_release_id"])

    async with get_async_db_context() as adb:
        if app_stub is not None:
            app = app_stub
        else:
            app = await _get_v1_chat_app_async(adb, app_id, workspace_id)
            await set_json_async(app_cache_key, {
                "id": str(app.id),
                "type": app.type,
                "current_release_id": (
                    str(app.current_release_id) if app.current_release_id else None
                ),
            }, ttl=60)

        if payload.version is not None:
            active_release = await _get_release_by_id_async(adb, app.id, payload.version)
        elif cached_release_id is not None:
            active_release = await _get_release_by_id_async(adb, app.id, cached_release_id)
        else:
            active_release = await _get_current_release_async(
                adb, app_id=app.id, workspace_id=workspace_id
            )
        if not active_release:
            raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

        end_user = await _get_or_create_public_end_user_async(
            adb,
            app_id=app.id,
            workspace_id=workspace_id,
            other_id=payload.user_id,
        )
        end_user_id = str(end_user.id)

        storage_type, user_rag_memory_id = await _prepare_public_memory_context_async(
            db=adb,
            workspace_id=workspace_id,
        )

        app_type = app.type
        _check_app_config(active_release)

        if app_type != AppType.PURE_WORKFLOW and not payload.message:
            raise BusinessException(
                "当前应用类型要求必须传入 message", BizCode.INVALID_PARAMETER
            )

        conversation_id = None
        if app_type != AppType.PURE_WORKFLOW or payload.conversation_id:
            conversation = await ConversationService(adb).create_or_get_conversation_async(
                app_id=app.id,
                workspace_id=workspace_id,
                user_id=end_user_id,
                is_draft=False,
                conversation_id=payload.conversation_id,
            )
            conversation_id = conversation.id

        runtime_config = None
        if app_type == AppType.AGENT:
            runtime_config = agent_config_4_app_release(active_release)
        elif app_type == AppType.MULTI_AGENT:
            runtime_config = multi_agent_config_4_app_release(active_release)
        elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            runtime_config = workflow_config_4_app_release(active_release)

        release_id = active_release.id

    if app_type == AppType.AGENT:
        # The thinking switch stays off unless the agent is configured for deep
        # thinking and the request explicitly asks for it.
        agent_config = runtime_config
        if not (agent_config.model_parameters.get("deep_thinking", False) and payload.thinking):
            agent_config.model_parameters["deep_thinking"] = False

    return ChatPreparation(
        app_id=app_id,
        app_type=app_type,
        workspace_id=workspace_id,
        end_user_id=end_user_id,
        conversation_id=conversation_id,
        payload=payload,
        runtime_config=runtime_config,
        release_id=release_id,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
    )


async def chat_stream(prep: ChatPreparation) -> AsyncIterator[str]:
    """Raw SSE chunks for the streaming legs (agent / multi-agent / workflow)."""

    payload = prep.payload

    if prep.app_type == AppType.AGENT:
        async with get_async_db_context() as stream_db:
            chat_service = AppChatService(stream_db)
            async for event in chat_service.agent_chat_stream(
                    message=payload.message,
                    conversation_id=prep.conversation_id,
                    user_id=prep.end_user_id,
                    variables=payload.variables,
                    web_search=True,
                    config=prep.runtime_config,
                    memory=True,
                    storage_type=prep.storage_type,
                    user_rag_memory_id=prep.user_rag_memory_id,
                    workspace_id=prep.workspace_id,
                    files=payload.files,
            ):
                yield event
        return

    if prep.app_type == AppType.MULTI_AGENT:
        async with get_async_db_context() as stream_db:
            chat_service = AppChatService(stream_db)
            async for event in chat_service.multi_agent_chat_stream(
                    message=payload.message,
                    conversation_id=prep.conversation_id,
                    user_id=prep.end_user_id,
                    variables=payload.variables,
                    config=prep.runtime_config,
                    web_search=True,
                    memory=True,
                    storage_type=prep.storage_type,
                    user_rag_memory_id=prep.user_rag_memory_id,
                    thinking=bool(payload.thinking),
                    files=payload.files,
            ):
                yield event
        return

    if prep.app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        async with get_async_db_context() as stream_db:
            chat_service = AppChatService(stream_db)
            async for event in chat_service.workflow_chat_stream(
                    message=payload.message,
                    conversation_id=prep.conversation_id,
                    user_id=prep.end_user_id,
                    variables=payload.variables,
                    files=payload.files,
                    config=prep.runtime_config,
                    web_search=True,
                    memory=True,
                    storage_type=prep.storage_type,
                    user_rag_memory_id=prep.user_rag_memory_id,
                    app_id=prep.app_id,
                    workspace_id=prep.workspace_id,
                    release_id=prep.release_id,
                    public=True,
            ):
                event_type = event.get("event", "message")
                event_data = event.get("data", {})
                yield (
                    f"event: {event_type}\ndata: "
                    f"{json.dumps(event_data, default=str, ensure_ascii=False)}\n\n"
                )
        return

    raise BusinessException(
        f"不支持的应用类型: {prep.app_type}", BizCode.APP_TYPE_NOT_SUPPORTED
    )


def parse_sse_chunk(chunk: str) -> tuple[str, bytes] | None:
    """One framed SSE chunk -> ``(event, data_bytes)``; comments yield None."""

    event = "message"
    data_lines: list[str] = []
    for line in chunk.splitlines():
        if not line or line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value or "message"
        elif field == "data":
            data_lines.append(value)
    if not data_lines:
        return None
    return event, "\n".join(data_lines).encode("utf-8")


async def chat_events(prep: ChatPreparation) -> AsyncIterator[tuple[str, bytes]]:
    """Structured frames for gRPC; keepalive comments stay transport-local."""

    async for chunk in with_sse_keepalive(chat_stream(prep)):
        parsed = parse_sse_chunk(chunk)
        if parsed is not None:
            yield parsed


async def chat_complete(prep: ChatPreparation) -> dict:
    """Non-streaming completion envelope (same shape as the HTTP route)."""

    payload = prep.payload

    if prep.app_type == AppType.AGENT:
        async with get_async_db_context() as sdb:
            result = await AppChatService(sdb).agent_chat(
                message=payload.message,
                conversation_id=prep.conversation_id,
                user_id=prep.end_user_id,
                variables=payload.variables,
                config=prep.runtime_config,
                web_search=True,
                memory=True,
                storage_type=prep.storage_type,
                user_rag_memory_id=prep.user_rag_memory_id,
                workspace_id=str(prep.workspace_id),
                files=payload.files,
            )
        return success(data=ChatResponse(**result).model_dump(mode="json"))

    if prep.app_type == AppType.MULTI_AGENT:
        async with get_async_db_context() as sdb:
            result = await AppChatService(sdb).multi_agent_chat(
                message=payload.message,
                conversation_id=prep.conversation_id,
                user_id=prep.end_user_id,
                variables=payload.variables,
                config=prep.runtime_config,
                web_search=True,
                memory=True,
                storage_type=prep.storage_type,
                user_rag_memory_id=prep.user_rag_memory_id,
                thinking=bool(payload.thinking),
                files=payload.files,
            )
        return success(data=ChatResponse(**result).model_dump(mode="json"))

    if prep.app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        async with get_async_db_context() as sdb:
            result = await AppChatService(sdb).workflow_chat(
                message=payload.message,
                conversation_id=prep.conversation_id,
                user_id=prep.end_user_id,
                variables=payload.variables,
                config=prep.runtime_config,
                web_search=True,
                memory=True,
                storage_type=prep.storage_type,
                user_rag_memory_id=prep.user_rag_memory_id,
                files=payload.files,
                app_id=prep.app_id,
                workspace_id=prep.workspace_id,
                release_id=prep.release_id,
            )
        return success(data=result, msg="工作流任务执行成功")

    raise BusinessException(
        f"不支持的应用类型: {prep.app_type}", BizCode.APP_TYPE_NOT_SUPPORTED
    )


async def resolve_v1_app_async(
    db: AsyncSession,
    *,
    api_key_id: str | None,
    workspace_id: UUID | None,
) -> UUID | None:
    """Async port of the ``/v1`` key-binding gate (same messages and codes)."""

    if not api_key_id:
        raise BusinessException("API Key 不存在", BizCode.API_KEY_NOT_FOUND)
    try:
        key_id = UUID(api_key_id)
    except (ValueError, TypeError) as error:
        raise BusinessException("API Key 无效或已过期", BizCode.API_KEY_INVALID) from error

    binding = (
        await cross_domain_read.get_api_key_binding_async(db, key_id, workspace_id)
        if workspace_id is not None
        else None
    )
    if binding is None:
        raise BusinessException("API Key 无效或已过期", BizCode.API_KEY_INVALID)

    if binding.resource_id and binding.type != "service":
        app = await db.get(App, binding.resource_id)
        if not app or not app.current_release_id:
            raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

    if "app" not in (binding.scopes or []):
        raise BusinessException("缺少必须的权限范围：app", BizCode.API_KEY_INVALID_SCOPE)

    return binding.resource_id


async def resolve_app_for_chat(
    *, api_key_id: str | None, workspace_id: UUID | None
) -> UUID:
    """Resolve the bound app of a chat call on its own async session."""

    async with get_async_db_context() as db:
        return require_bound_app_id(
            await resolve_v1_app_async(
                db, api_key_id=api_key_id, workspace_id=workspace_id
            )
        )


__all__ = [
    "ChatPreparation",
    "chat_complete",
    "chat_events",
    "chat_stream",
    "parse_sse_chunk",
    "prepare_chat",
    "require_bound_app_id",
    "resolve_app_for_chat",
    "resolve_v1_app_async",
]
