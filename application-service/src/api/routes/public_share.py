"""Public share-view routes (share-token access to released apps).

Ported surface of the monolith ``public_share_controller``: access-token
exchange, share info / password / embed / config, conversation list and
detail, share chat (agent / multi-agent / workflow) and reply regeneration,
human-intervention submit and resume, plus the message-interaction and
conversation-share copies of the console API.
Viewers hold no platform credentials — every request authenticates with a
share access token minted by ``/{share_token}/token`` and the gateway forwards
these paths without terminating a user credential (see ``auth.py`` for the
public-path exemption).

Streaming contract: SSE generators open their own DB session, because the
request-scoped dependency session is already torn down when the response body
starts streaming. The chat endpoints use the async session; regeneration runs
on sync-db services and uses the sync session context instead. Every stream is
wrapped by the shared keep-alive helper (heartbeat comments while silent, idle
cutoff ahead of the gateway watchdog).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...db import get_async_db_context, get_db_context
from ...errors import BizCode, BusinessException, ResourceNotFoundException
from ...models import (
    App,
    AppRelease,
    AppType,
    FileMetadata,
    HitLogSource,
    Message,
    MessageFeedback,
    WorkflowExecution,
    Workspace,
)
from ...quota.alerts import report_quota_change, report_quota_change_sync
from ...quota.usage import check_end_user_quota_async, check_quota
from ...repositories import knowledge_repository
from ...repositories.end_user_repository import EndUserRepository
from ...repositories.workflow_repository import WorkflowConfigRepository
from ...schemas import app_schema, conversation_schema, release_share_schema
from ...services import workspace_service
from ...services.app_chat_service import AppChatService, assert_not_opening_statement
from ...services.app_log_service import AppLogService
from ...services.app_service import AppService
from ...services.conversation_service import ConversationService
from ...services.conversation_share_service import ConversationShareService
from ...services.intervention_registry import submit_intervention
from ...services.message_feedback_service import FavoriteService, FeedbackService
from ...services.message_report_service import ReportService
from ...services.multi_agent_service import MultiAgentService
from ...services.release_share_service import ReleaseShareService
from ...services.shared_chat_service import SharedChatService
from ...services.workflow_service import WorkflowService
from ...utils.app_config_utils import (
    agent_config_4_app_release,
    multi_agent_config_4_app_release,
    workflow_config_4_app_release,
)
from ...utils.datetime_utils import parse_iso_to_utc_naive, to_timestamp_ms
from ...utils.file_urls import build_permanent_file_url
from ...utils.redis_cache import CACHE_MISS, get_json_async, redis_cache, set_json_async
from ...utils.share_token import create_access_token
from ...utils.sse_stream import sse_response
from ..dependencies import ShareTokenData, get_share_user_id, get_sync_db
from ..schemas.common import fail, success
from ..schemas.response_schema import PageData, PageMeta

router = APIRouter(prefix="/public/share", tags=["Public Share"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
ShareViewer = Annotated[ShareTokenData, Depends(get_share_user_id)]

# Only this subset of SSE events is forwarded to the share view: the message
# text stream, the endpoint events (start / end / error / regenerate_end) and
# content-review replacements. node-*/workflow-*/cycle_item/intervention_*
# stay internal.
SHARE_FORWARD_EVENTS = frozenset({
    "start",
    "message",
    "message_replace",
    "tool_start",
    "memory_stage",
    "tool_end",
    "tool_error",
    "end",
    "error",
    "regenerate_end",
})

# Matches the `event: <type>` line inside an already-serialized SSE string
# (the agent path yields framed SSE text rather than event dicts).
SSE_EVENT_LINE = re.compile(r"^event:\s*(\S+)\s*$", re.MULTILINE)


def _to_ms(iso_str: str | None) -> int | None:
    """ISO 8601 timestamp string to unix milliseconds; None on bad input."""

    if not iso_str:
        return None
    try:
        return to_timestamp_ms(parse_iso_to_utc_naive(iso_str))
    except (ValueError, TypeError):
        return None


def get_base_url(request: Request) -> str:
    """Origin (scheme + host) of the incoming request.

    Behind the gateway this service only sees its own internal URL, so the
    origin is rebuilt from the ``X-Forwarded-Proto`` / ``X-Forwarded-Host``
    headers the gateway injects, falling back to the request origin when they
    are absent (direct calls in tests / local runs).
    """

    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}"


def get_or_generate_user_id(payload_user_id: str | None, request: Request) -> str:
    """Resolve the viewer id: the supplied one wins, otherwise a stable guest
    id derived from the client IP and User-Agent."""

    if payload_user_id:
        return payload_user_id

    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")

    unique_string = f"{client_ip}_{user_agent}"
    hash_value = hashlib.md5(unique_string.encode()).hexdigest()[:16]

    return f"guest_{hash_value}"


@redis_cache(ttl=120, prefix="storage_type", skip_args=["db"], id_arg="workspace_id")
async def _prepare_public_memory_context_async(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> tuple[str, str]:
    """Resolve (storage_type, user_rag_memory_id) for a share chat.

    Only RAG workspaces hit the knowledge store; a missing USER_RAG_MERORY
    knowledge base warns and degrades the workspace to neo4j storage.
    """

    storage_type = await workspace_service.get_workspace_storage_type_without_auth_async(
        db=db,
        workspace_id=workspace_id,
    )
    if storage_type is None:
        storage_type = "neo4j"

    user_rag_memory_id = ""
    if storage_type == "rag":
        knowledge = await knowledge_repository.get_knowledge_by_name_async(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id,
        )
        if knowledge:
            user_rag_memory_id = str(knowledge.id)
        else:
            logger.warning(
                f"未找到名为 'USER_RAG_MERORY' 的知识库，"
                f"workspace_id: {workspace_id}，将使用 neo4j 存储"
            )
            storage_type = "neo4j"

    return storage_type, user_rag_memory_id


async def _get_or_create_public_end_user_async(
    db: AsyncSession,
    *,
    app_id: uuid.UUID,
    workspace_id: uuid.UUID,
    other_id: str,
    original_user_id: str | None = None,
):
    """Resolve the share viewer's end user, creating it on first contact.

    Cached by (workspace, other_id); a fresh user runs the end-user admission
    check and the post-create alert evaluation.
    """

    end_user_repo = EndUserRepository(db)

    cache_key = f"cache:v2:end_user:{workspace_id}:{other_id}"
    cached = await get_json_async(cache_key)
    if cached is not CACHE_MISS:
        cached_id = cached.get("id")
        if cached_id and cached.get("app_id") == str(app_id):
            return SimpleNamespace(id=uuid.UUID(cached_id), app_id=app_id)

    existing_end_user = await end_user_repo.get_end_user_by_other_id_async(
        workspace_id=workspace_id,
        other_id=other_id,
    )
    if existing_end_user is not None:
        if existing_end_user.app_id != app_id:
            existing_end_user.app_id = app_id
            await db.commit()
        await set_json_async(cache_key, {
            "id": str(existing_end_user.id),
            "app_id": str(existing_end_user.app_id),
        }, ttl=120)
        return existing_end_user

    workspace = await db.get(Workspace, workspace_id)
    # The quota check may roll back this AsyncSession (premium tables missing)
    # and expire ORM instances; save the scalar first and never touch
    # workspace.* afterwards to avoid a MissingGreenlet on attribute refresh.
    workspace_tenant_id = workspace.tenant_id if workspace is not None else None
    if workspace_tenant_id is not None:
        await check_end_user_quota_async(
            db,
            workspace_tenant_id,
            workspace_id=workspace_id,
        )

    new_user = await end_user_repo.get_or_create_end_user_async(
        app_id=app_id,
        workspace_id=workspace_id,
        other_id=other_id,
        original_user_id=original_user_id,
    )
    # The end user is persisted and usage actually changed: evaluate alerts now.
    if workspace_tenant_id is not None:
        await report_quota_change(
            workspace_tenant_id,
            "end_user_quota",
            workspace_id=workspace_id,
        )
    await set_json_async(cache_key, {
        "id": str(new_user.id),
        "app_id": str(new_user.app_id),
    }, ttl=120)
    return new_user


@router.post(
    "/{share_token}/token",
    summary="获取访问 token",
)
def get_access_token(
    share_token: str,
    payload: release_share_schema.TokenRequest,
    request: Request,
    db: DbSession,
):
    """Exchange a viewer id + share token for an access token.

    A password-protected share requires the password here; the returned token
    then covers every subsequent share request.
    """

    user_id = get_or_generate_user_id(payload.user_id, request)

    service = ReleaseShareService(db)
    try:
        service.get_shared_release_info(
            share_token=share_token,
            password=payload.password,
        )
    except Exception as e:
        logger.error(f"获取分享信息失败: {str(e)}")
        raise

    access_token = create_access_token(user_id, share_token)

    logger.info(
        "生成访问 token",
        extra={
            "share_token": share_token,
            "user_id": user_id,
        },
    )

    return success(data={
        "access_token": access_token,
        "token_type": "Bearer",
        "user_id": user_id,
    })


@router.post(
    "/chat",
    summary="发送消息（支持流式和非流式）",
)
async def chat(
    payload: conversation_schema.ChatRequest,
    share_data: ShareViewer,
):
    """Send a message to the shared app and get a reply.

    The bearer access token is decoded by the dependency; user_id and
    share_token come from it. Multi-turn via conversation_id; a missing
    conversation_id auto-creates one. Every validation finishes before the
    streaming response starts so failures return clean HTTP errors.
    """

    user_id = share_data.user_id
    share_token = share_data.share_token
    password = None  # Token authentication carries no password
    other_id = user_id

    # share_token cache: a hit carrying the release id skips the share/app
    # lookups and re-fetches only the release row.
    share_cache_key = f"cache:v2:share_token:{share_token}"
    share_cache = await get_json_async(share_cache_key)
    _cached_app_id = None
    _cached_workspace_id = None
    _cached_app_type = None
    _cached_release_id = None
    if share_cache is not CACHE_MISS:
        _cached_app_id = uuid.UUID(share_cache["app_id"])
        _cached_workspace_id = uuid.UUID(share_cache["workspace_id"])
        _cached_app_type = share_cache["app_type"]
        if share_cache.get("release_id"):
            _cached_release_id = uuid.UUID(share_cache["release_id"])

    try:
        async with get_async_db_context() as db:
            if _cached_app_id is not None and _cached_release_id is not None:
                release = await db.get(AppRelease, _cached_release_id)
                if not release:
                    raise BusinessException("发布版本不存在", BizCode.NOT_FOUND)
                app_id = _cached_app_id
                workspace_id = _cached_workspace_id
                app_type = _cached_app_type
            else:
                service = SharedChatService(db)
                share, release = await service.get_release_by_share_token_async(
                    share_token, password
                )

                app = await db.scalar(
                    select(App)
                    .where(App.id == share.app_id, App.is_active.is_(True))
                    .limit(1)
                )
                if not app:
                    raise ResourceNotFoundException("应用", str(share.app_id))
                workspace_id = app.workspace_id
                app_id = app.id
                app_type = app.type

                await set_json_async(share_cache_key, {
                    "app_id": str(app_id),
                    "release_id": str(release.id),
                    "workspace_id": str(workspace_id),
                    "app_type": app_type,
                }, ttl=60)

            new_end_user = await _get_or_create_public_end_user_async(
                db,
                app_id=app_id,
                workspace_id=workspace_id,
                other_id=other_id,
                original_user_id=user_id,
            )
            end_user_id = str(new_end_user.id)

            storage_type, user_rag_memory_id = await _prepare_public_memory_context_async(
                db=db,
                workspace_id=workspace_id,
            )

            runtime_config = None
            if app_type == AppType.AGENT:
                model_config_id = release.default_model_config_id
                if not model_config_id:
                    raise BusinessException("Agent 应用未配置模型", BizCode.AGENT_CONFIG_MISSING)
                runtime_config = agent_config_4_app_release(release)
            elif app_type == AppType.MULTI_AGENT:
                config = release.config or {}
                # supervisor_loop accepts zero sub-agents (the supervisor degrades to a
                # bare agent); every other mode requires at least one sub-agent.
                if (
                    not config.get("sub_agents")
                    and config.get("orchestration_mode") != "supervisor_loop"
                ):
                    raise BusinessException(
                        "多 Agent 应用未配置子 Agent", BizCode.AGENT_CONFIG_MISSING
                    )
                runtime_config = multi_agent_config_4_app_release(release)
            elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
                runtime_config = workflow_config_4_app_release(release)
                if not runtime_config.id:
                    wf_repo = WorkflowConfigRepository(db)
                    source_config = await wf_repo.get_by_app_id_async(release.app_id)
                    if source_config:
                        runtime_config.id = source_config.id
                if runtime_config.id:
                    # Snapshot ids arrive as JSON strings; the backfill above
                    # assigns a UUID directly, so str() keeps both paths working.
                    runtime_config.id = uuid.UUID(str(runtime_config.id))
            else:
                raise BusinessException(
                    f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED
                )

            if app_type != AppType.PURE_WORKFLOW and not payload.message:
                raise BusinessException(
                    "当前应用类型要求必须传入 message", BizCode.INVALID_PARAMETER
                )

            conversation_id = None
            if app_type != AppType.PURE_WORKFLOW or payload.conversation_id:
                conv_service = ConversationService(db)
                conversation = await conv_service.create_or_get_conversation_async(
                    app_id=app_id,
                    workspace_id=workspace_id,
                    user_id=end_user_id,
                    is_draft=False,
                    conversation_id=payload.conversation_id,
                )
                conversation_id = conversation.id

            app_id = release.app_id
            release_id = release.id

        logger.debug(
            "参数验证完成",
            extra={
                "share_token": share_token,
                "app_type": app_type,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "stream": payload.stream,
            },
        )

    except Exception as e:
        # Validation failed: re-raise for the global exception handler.
        logger.error(f"参数验证失败: {str(e)}")
        raise

    if app_type == AppType.AGENT:
        agent_config = runtime_config

        if not (agent_config.model_parameters.get("deep_thinking", False) and payload.thinking):
            agent_config.model_parameters["deep_thinking"] = False

        source = HitLogSource.EXTERNAL
        # In-process execution only; sandbox execution is not part of this
        # service (the flag is kept for parity with the host signature).
        execution_mode = "in_process"

        if payload.stream:
            async def event_generator():
                async with get_async_db_context() as stream_db:
                    _app_chat_service = AppChatService(stream_db)
                    async for event in _app_chat_service.agent_chat_stream(
                            message=payload.message,
                            conversation_id=conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            web_search=payload.web_search,
                            config=agent_config,
                            memory=payload.memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            workspace_id=workspace_id,
                            files=payload.files,
                            source=source,
                            execution_mode=execution_mode,
                    ):
                        yield event

            return sse_response(event_generator())

        async with get_async_db_context() as db:
            app_chat_service = AppChatService(db)
            result = await app_chat_service.agent_chat(
                message=payload.message,
                conversation_id=conversation_id,
                user_id=end_user_id,
                variables=payload.variables,
                config=agent_config,
                web_search=payload.web_search,
                memory=payload.memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                workspace_id=workspace_id,
                files=payload.files,
                source=source,
                execution_mode=execution_mode,
            )
        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))

    if app_type == AppType.MULTI_AGENT:
        config = runtime_config

        if payload.stream:
            async def event_generator():
                async with get_async_db_context() as stream_db:
                    _app_chat_service = AppChatService(stream_db)
                    async for event in _app_chat_service.multi_agent_chat_stream(
                            message=payload.message,
                            conversation_id=conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            config=config,
                            web_search=payload.web_search,
                            memory=payload.memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            thinking=bool(getattr(payload, "thinking", False)),
                            execution_mode="in_process",
                            files=payload.files,
                    ):
                        yield event

            return sse_response(event_generator())

        async with get_async_db_context() as db:
            app_chat_service = AppChatService(db)
            result = await app_chat_service.multi_agent_chat(
                message=payload.message,
                conversation_id=conversation_id,
                user_id=end_user_id,
                variables=payload.variables,
                config=config,
                web_search=payload.web_search,
                memory=payload.memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                thinking=bool(getattr(payload, "thinking", False)),
                execution_mode="in_process",
                files=payload.files,
            )

        return success(data=conversation_schema.ChatResponse(**result).model_dump(mode="json"))

    if app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        config = runtime_config

        if payload.stream:
            source = HitLogSource.EXTERNAL

            async def event_generator():
                async with get_async_db_context() as stream_db:
                    _app_chat_service = AppChatService(stream_db)
                    async for event in _app_chat_service.workflow_chat_stream(
                            message=payload.message,
                            conversation_id=conversation_id,
                            user_id=end_user_id,
                            variables=payload.variables,
                            files=payload.files,
                            config=config,
                            web_search=payload.web_search,
                            memory=payload.memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            app_id=app_id,
                            workspace_id=workspace_id,
                            release_id=release_id,
                            public=True,
                            source=source,
                    ):
                        event_type = event.get("event", "message")
                        event_data = event.get("data", {})
                        sse_message = (
                            f"event: {event_type}\ndata: "
                            f"{json.dumps(event_data, default=str, ensure_ascii=False)}\n\n"
                        )
                        yield sse_message

            return sse_response(event_generator())

        source = HitLogSource.EXTERNAL
        async with get_async_db_context() as db:
            app_chat_service = AppChatService(db)
            result = await app_chat_service.workflow_chat(
                message=payload.message,
                conversation_id=conversation_id,
                user_id=end_user_id,
                variables=payload.variables,
                files=payload.files,
                config=config,
                web_search=payload.web_search,
                memory=payload.memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                app_id=app_id,
                workspace_id=workspace_id,
                release_id=release_id,
                source=source,
            )
        logger.debug(
            "工作流试运行返回结果",
            extra={
                "result_type": str(type(result)),
                "has_response": "response" in result if isinstance(result, dict) else False,
            },
        )
        return success(
            data=result,
            msg="工作流任务执行成功",
        )

    raise BusinessException(f"不支持的应用类型: {app_type}", BizCode.APP_TYPE_NOT_SUPPORTED)


@router.post(
    "/messages/{message_id}/regenerate",
    summary="重新生成回复",
)
async def regenerate_message(
    message_id: uuid.UUID,
    payload: app_schema.RegenerateRequest,
    share_data: ShareViewer,
    db: DbSession,
):
    """Regenerate an assistant reply (multi-version, streaming or not).

    Keeps the same conversation context and user question; only the answer is
    re-generated and stacked as a new version. The opening statement cannot be
    regenerated — that is rejected before the SSE response starts.
    """

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    # Must run before the StreamingResponse: once SSE has started a failure
    # cannot be turned into a clean HTTP error, so the front end would show a
    # blank page instead of a warning toast.
    assert_not_opening_statement(db, message_id)

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=share_data.user_id,
    )

    app_type = release.app.type if release.app else None

    if app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        if app_type == AppType.PURE_WORKFLOW:
            raise BusinessException(
                code=BizCode.APP_TYPE_NOT_SUPPORTED,
                message="纯工作流为一次性执行，不支持重新生成",
            )

        workflow_service = WorkflowService(db)
        # Rebuild the config from the release snapshot (restoring the real
        # config id and workflow_type). Not workflow_config_4_app_release: that
        # one leaves workflow_type unset, and _supports_conversation would
        # misjudge the flow as non-conversational.
        config = AppService(db)._workflow_config_from_release(release)

        if not workflow_service._supports_conversation(config):
            raise BusinessException(
                code=BizCode.BAD_REQUEST,
                message="该工作流非对话流，不支持重新生成",
            )

        # Version-cap check must also run before the SSE response starts.
        workflow_service._assert_regenerate_quota(message_id)

        if payload.stream:
            async def event_generator():
                with get_db_context() as stream_db:
                    svc = WorkflowService(stream_db)
                    async for event in svc.regenerate_stream(
                            message_id=message_id,
                            app_id=release.app_id,
                            config=config,
                            workspace_id=workspace_id,
                            user_id=str(end_user.id),
                            override_variables=payload.variables,
                            # carry the real execution_id on the start event
                            pre_create_execution=True,
                    ):
                        event_type = event.get("event", "message")
                        if event_type not in SHARE_FORWARD_EVENTS:
                            continue
                        event_data = event.get("data", {})
                        yield (
                            f"event: {event_type}\ndata: "
                            f"{json.dumps(event_data, default=str, ensure_ascii=False)}\n\n"
                        )

            return sse_response(event_generator())

        result = await workflow_service.regenerate(
            message_id=message_id,
            app_id=release.app_id,
            config=config,
            workspace_id=workspace_id,
            user_id=str(end_user.id),
            override_variables=payload.variables,
        )
        return success(data=app_schema.WorkflowRegenerateResponse(**result))

    # ---- multi-agent cluster branch (MULTI_AGENT) ----
    # Reruns through the orchestrator (keeping supervisor routing / sub-agent
    # orchestration), with the config taken from the published snapshot
    # (multi_agent_config_4_app_release, same source as the share-side chat
    # entry). Cluster traffic used to be rejected by the "non-AGENT" guard
    # below. SSE events are whitelisted through SHARE_FORWARD_EVENTS (internal
    # process events such as sub_agent_*/agent_dispatch are dropped; only the
    # text stream and endpoint events pass through).
    if app_type == AppType.MULTI_AGENT:
        cluster_cfg = multi_agent_config_4_app_release(release)
        # supervisor_loop allows zero sub-agents (the supervisor degrades to a
        # bare agent); other modes require at least one
        if not (cluster_cfg.sub_agents or []) and cluster_cfg.orchestration_mode != (
            "supervisor_loop"
        ):
            raise BusinessException("多 Agent 应用未配置子 Agent", BizCode.AGENT_CONFIG_MISSING)

        # Storage / memory context: same source as the AGENT branch below (that
        # branch computes its own after this one, so its locals can't be reused —
        # compute an independent copy here)
        storage_type = workspace_service.get_workspace_storage_type_without_auth(
            db=db, workspace_id=workspace_id
        )
        if storage_type is None:
            storage_type = "neo4j"
        user_rag_memory_id = ""
        if storage_type == "rag" and workspace_id:
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=db, name="USER_RAG_MERORY", workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)

        # Deep thinking: requires both the snapshot's model_parameters flag and
        # the request flag; otherwise it stays off (aligns with the AGENT
        # branch's deep_thinking handling; cluster thinking is consumed by the
        # orchestrator's _prepare_turn_chat_features)
        thinking = bool(getattr(payload, "thinking", False)) and bool(
            (cluster_cfg.model_parameters or {}).get("deep_thinking", False)
        )

        if payload.stream:
            async def cluster_event_generator():
                with get_db_context() as stream_db:
                    svc = MultiAgentService(stream_db)
                    async for event in svc.regenerate_stream(
                            message_id=message_id,
                            app_id=release.app_id,
                            user_id=str(end_user.id),
                            variables=payload.variables,
                            web_search=payload.web_search,
                            memory=payload.memory,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            thinking=thinking,
                            config=cluster_cfg,
                            source=HitLogSource.EXTERNAL,
                    ):
                        # The cluster path yields serialized SSE strings; filter
                        # on the `event:` line against the whitelist
                        m = SSE_EVENT_LINE.search(event)
                        if m and m.group(1) not in SHARE_FORWARD_EVENTS:
                            continue
                        yield event

            return sse_response(cluster_event_generator())

        result = await MultiAgentService(db).regenerate(
            message_id=message_id,
            app_id=release.app_id,
            user_id=str(end_user.id),
            variables=payload.variables,
            web_search=payload.web_search,
            memory=payload.memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            thinking=thinking,
            config=cluster_cfg,
            source=HitLogSource.EXTERNAL,
        )
        return success(data=app_schema.RegenerateResponse(**result))

    if app_type != AppType.AGENT:
        raise BusinessException(
            code=BizCode.APP_TYPE_NOT_SUPPORTED,
            message=f"不支持的应用类型: {app_type}",
        )

    agent_cfg = agent_config_4_app_release(release)

    if not (agent_cfg.model_parameters.get("deep_thinking", False) and payload.thinking):
        agent_cfg.model_parameters["deep_thinking"] = False

    storage_type = workspace_service.get_workspace_storage_type_without_auth(
        db=db, workspace_id=workspace_id
    )
    if storage_type is None:
        storage_type = "neo4j"
    user_rag_memory_id = ""
    if storage_type == "rag":
        if workspace_id:
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=db, name="USER_RAG_MERORY", workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)

    if payload.stream:
        async def event_generator():
            with get_db_context() as stream_db:
                svc = AppChatService(stream_db)
                async for event in svc.regenerate_stream(
                        message_id=message_id,
                        config=agent_cfg,
                        workspace_id=workspace_id,
                        user_id=str(end_user.id),
                        variables=payload.variables,
                        web_search=payload.web_search,
                        memory=payload.memory,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                ):
                    # The agent path yields serialized SSE strings; filter on the
                    # `event:` line to drop internal events (reasoning/agent_log).
                    m = SSE_EVENT_LINE.search(event)
                    if m and m.group(1) not in SHARE_FORWARD_EVENTS:
                        continue
                    yield event

        return sse_response(event_generator())

    result = await AppChatService(db).regenerate(
        message_id=message_id,
        config=agent_cfg,
        workspace_id=workspace_id,
        user_id=str(end_user.id),
        variables=payload.variables,
        web_search=payload.web_search,
        memory=payload.memory,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
    )

    return success(data=app_schema.RegenerateResponse(**result))


@router.get(
    "",
    summary="获取公开分享的应用信息",
)
def get_shared_release(
    share_data: ShareViewer,
    db: DbSession,
    password: str | None = Query(None, description="访问密码（如果需要）"),
):
    """Share-view resolution.

    A password-protected share without the password returns the partial info
    (no config detail) so the viewer can render the password prompt.
    """

    service = ReleaseShareService(db)
    info = service.get_shared_release_info(
        share_token=share_data.share_token,
        password=password,
    )

    return success(data=info)


@router.post(
    "/verify",
    summary="验证访问密码",
)
def verify_password(
    payload: release_share_schema.PasswordVerifyRequest,
    share_data: ShareViewer,
    db: DbSession,
):
    """Check one password against the share link ahead of the full fetch."""

    service = ReleaseShareService(db)
    is_valid = service.verify_password(
        share_token=share_data.share_token,
        password=payload.password,
    )

    return success(data={"valid": is_valid})


@router.get(
    "/embed",
    summary="获取嵌入代码",
)
def get_embed_code(
    request: Request,
    share_data: ShareViewer,
    db: DbSession,
    width: str = Query("100%", description="iframe 宽度"),
    height: str = Query("600px", description="iframe 高度"),
):
    """iframe embed snippet for this share link, with custom dimensions."""

    base_url = get_base_url(request)

    service = ReleaseShareService(db)
    embed_code = service.get_embed_code(
        share_token=share_data.share_token,
        width=width,
        height=height,
        base_url=base_url,
    )

    return success(data=embed_code)


@router.get(
    "/conversations",
    summary="获取会话列表",
)
def list_conversations(
    share_data: ShareViewer,
    db: DbSession,
    password: str | None = Query(None, description="访问密码"),
    page: int = Query(1, ge=1),
    pagesize: int = Query(20, ge=1, le=100),
):
    """Conversations of the shared app for this viewer, paginated.

    The viewer's end user is admitted here: a missing one runs the quota
    check; the alert evaluation runs only when this call really created the
    row.
    """

    other_id = share_data.user_id
    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, password)
    workspace_id = release.app.workspace_id

    end_user_repo = EndUserRepository(db)
    existing_end_user = end_user_repo.get_end_user_by_other_id(
        workspace_id=workspace_id,
        other_id=other_id,
    )
    new_user_tenant_id = None
    if existing_end_user is None:
        workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if workspace:
            new_user_tenant_id = workspace.tenant_id
            check_quota(
                db,
                workspace.tenant_id,
                "end_user_quota",
                "end_user",
                workspace_id=workspace_id,
            )

    new_end_user, created = end_user_repo.get_or_create_end_user_with_status(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=other_id,
    )
    if created and new_user_tenant_id is not None:
        report_quota_change_sync(
            new_user_tenant_id,
            "end_user_quota",
            workspace_id=workspace_id,
        )

    conversations, total = service.list_conversations(
        share_token=share_data.share_token,
        user_id=str(new_end_user.id),
        password=password,
        page=page,
        pagesize=pagesize,
    )

    items = [conversation_schema.Conversation.model_validate(c) for c in conversations]
    meta = PageMeta(
        page=page,
        pagesize=pagesize,
        total=total,
        hasnext=(page * pagesize) < total,
    )

    return success(data=PageData(page=meta, items=items))


@router.get(
    "/conversations/{conversation_id}",
    summary="获取会话详情（含消息）",
)
def get_conversation(
    conversation_id: uuid.UUID,
    share_data: ShareViewer,
    db: DbSession,
    password: str | None = Query(None, description="访问密码"),
):
    """Conversation detail with message history.

    Messages carry audio status, the viewer's feedback / favorite state, local
    attachments resolved to accessible URLs, and the aggregated
    human-intervention map.
    """

    chat_service = SharedChatService(db)
    conversation = chat_service.get_conversation_messages(
        share_token=share_data.share_token,
        conversation_id=conversation_id,
        password=password,
    )

    # All messages including multi-version replies.
    conv_service = ConversationService(db)
    messages = conv_service.get_conversation_with_messages(conversation_id)

    file_ids = []
    message_file_id_map = {}

    # First pass: resolve audio_url strings to file ids.
    for idx, m in enumerate(messages):
        if isinstance(m, list):
            for ver_idx, ver_msg in enumerate(m):
                if ver_msg.role == "assistant" and ver_msg.meta_data:
                    audio_url = ver_msg.meta_data.get("audio_url")
                    if not audio_url:
                        continue
                    try:
                        file_id = uuid.UUID(audio_url.rstrip("/").split("/")[-1])
                    except (ValueError, IndexError):
                        ver_msg.meta_data["audio_status"] = "unknown"
                        continue
                    file_ids.append(file_id)
                    message_file_id_map[(idx, ver_idx)] = file_id
        else:
            if m.role == "assistant" and m.meta_data:
                audio_url = m.meta_data.get("audio_url")
                if not audio_url:
                    continue
                try:
                    file_id = uuid.UUID(audio_url.rstrip("/").split("/")[-1])
                except (ValueError, IndexError):
                    m.meta_data["audio_status"] = "unknown"
                    continue
                file_ids.append(file_id)
                message_file_id_map[(idx, None)] = file_id

    file_status_map = {}
    if file_ids:
        file_metas = (
            db.query(FileMetadata)
            .filter(FileMetadata.id.in_(set(file_ids)))
            .all()
        )
        file_status_map = {fm.id: fm.status for fm in file_metas}

    # Second pass: map the file statuses back onto the messages.
    for (idx, ver_idx), file_id in message_file_id_map.items():
        status = file_status_map.get(file_id, "unknown")
        if ver_idx is not None:
            messages[idx][ver_idx].meta_data["audio_status"] = status
        else:
            messages[idx].meta_data["audio_status"] = status

    # The current viewer's favorites and feedback, batch-loaded.
    message_ids: set[uuid.UUID] = set()
    for m in messages:
        if isinstance(m, list):
            for ver_msg in m:
                if ver_msg.id:
                    message_ids.add(ver_msg.id)
        else:
            if m.id:
                message_ids.add(m.id)
    favorited_ids: set[uuid.UUID] = set()
    if message_ids:
        favorited_rows = (
            db.query(MessageFeedback.message_id)
            .filter(
                MessageFeedback.message_id.in_(message_ids),
                MessageFeedback.user_id == share_data.user_id,
                MessageFeedback.is_favorite.is_(True),
            )
            .all()
        )
        favorited_ids = {row[0] for row in favorited_rows}

    # Per-viewer feedback rows: taking an arbitrary row would mix viewers.
    feedback_map: dict[uuid.UUID, tuple[str | None, str | None]] = {}
    if message_ids:
        feedback_rows = (
            db.query(
                MessageFeedback.message_id,
                MessageFeedback.feedback_type,
                MessageFeedback.feedback_content,
            )
            .filter(
                MessageFeedback.message_id.in_(message_ids),
                MessageFeedback.user_id == share_data.user_id,
            )
            .all()
        )
        feedback_map = {row[0]: (row[1], row[2]) for row in feedback_rows}

    # Local attachments persist only the upload_file_id (url empty): collect
    # them, batch-query, and resolve to accessible URLs.
    local_file_ids: set[uuid.UUID] = set()
    for m in messages:
        msg_list = m if isinstance(m, list) else [m]
        for msg in msg_list:
            if isinstance(msg.meta_data, dict):
                for f in msg.meta_data.get("files") or []:
                    if isinstance(f, dict) and not f.get("url") and f.get("upload_file_id"):
                        try:
                            local_file_ids.add(uuid.UUID(str(f["upload_file_id"])))
                        except (ValueError, TypeError):
                            continue
    local_file_url_map: dict[str, str] = {}
    if local_file_ids:
        completed_files = db.query(FileMetadata).filter(
            FileMetadata.id.in_(local_file_ids),
            FileMetadata.workspace_id == conversation.workspace_id,
            FileMetadata.status == "completed",
        ).all()
        local_file_url_map = {
            str(f.id): build_permanent_file_url(f.id) for f in completed_files
        }

    def _resolve_meta(msg) -> dict | None:
        """Resolve local attachments in meta_data.files to accessible URLs
        without mutating the ORM row."""

        meta = msg.meta_data
        if not isinstance(meta, dict) or not meta.get("files"):
            return meta
        resolved_files = []
        for f in meta["files"]:
            if not isinstance(f, dict):
                continue
            file_url = f.get("url") or local_file_url_map.get(str(f.get("upload_file_id")))
            resolved_files.append({**f, "url": file_url} if file_url else f)
        return {**meta, "files": resolved_files}

    message_responses = []
    for m in messages:
        if isinstance(m, list):
            version_responses = []
            for ver_msg in m:
                fb_type, fb_content = feedback_map.get(ver_msg.id, (None, None))
                version_responses.append(conversation_schema.Message(
                    id=ver_msg.id,
                    conversation_id=ver_msg.conversation_id,
                    role=ver_msg.role,
                    content=ver_msg.content,
                    status=ver_msg.status,
                    meta_data=_resolve_meta(ver_msg),
                    created_at=ver_msg.created_at,
                    feedback_type=fb_type,
                    feedback_content=fb_content,
                    is_favorited=ver_msg.id in favorited_ids,
                    version=ver_msg.version,
                    is_current=ver_msg.is_current,
                    parent_message_id=ver_msg.parent_message_id,
                ))
            message_responses.append(version_responses)
        else:
            fb_type, fb_content = feedback_map.get(m.id, (None, None))
            message_responses.append(conversation_schema.Message(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,
                content=m.content,
                status=m.status,
                meta_data=_resolve_meta(m),
                created_at=m.created_at,
                feedback_type=fb_type,
                feedback_content=fb_content,
                is_favorited=m.id in favorited_ids,
                version=m.version,
                is_current=m.is_current,
                parent_message_id=m.parent_message_id,
            ))

    conv_dict = (
        conversation_schema.Conversation.model_validate(conversation)
        .model_dump(mode="json")
    )
    conv_dict["messages"] = message_responses

    # Human-intervention data aggregated across the conversation's executions,
    # keyed by message id: { message_id: { execution_id, status,
    # interventions } }. `interventions` is the per-node merge of
    # resolved_interventions with the pending snapshot, so several
    # intervention nodes within one waiting cycle are all preserved.
    intervention_map: dict[str, dict] = {}
    for wf_exec in db.query(WorkflowExecution).filter(
        WorkflowExecution.conversation_id == conversation_id,
    ).order_by(WorkflowExecution.created_at.asc()):
        intr_ctx = (wf_exec.context or {}).get("human_intervention", {})
        if not intr_ctx:
            continue
        message_id = intr_ctx.get("message_id")
        if not message_id:
            continue

        resolved_list = intr_ctx.get("resolved_interventions") or []
        pending_list = intr_ctx.get("interventions") or []

        merged_by_node: dict[str, dict] = {
            i["node_id"]: dict(i) for i in resolved_list if i.get("node_id")
        }
        for i in pending_list:
            nid = i.get("node_id")
            if not nid:
                continue
            base = merged_by_node.get(nid, {})
            merged = dict(base)
            for k, v in i.items():
                if k in (
                    "resolved_action_id",
                    "resolved_form_data",
                    "resolved_at",
                    "resolved_kind",
                ):
                    # A stale pending snapshot must not wipe resolved data.
                    if base.get(k) in (None, "", []):
                        merged[k] = v
                else:
                    merged[k] = v
            merged_by_node[nid] = merged

        # Stable order: resolved nodes (by resolved_at) first, never-resolved
        # pending nodes at the end.
        ordered = sorted(
            merged_by_node.values(),
            key=lambda item: (
                item.get("resolved_at") or "9999-12-31T23:59:59",
                item.get("node_id") or "",
            ),
        )

        def _public_resolved_kind(kind: str | None):
            if kind == "pending":
                return "interrupt"
            return kind

        # Resolved means every intervention node carries an outcome: a HITL
        # node taking its timeout branch counts as resolved even if the
        # overall workflow later fails downstream.
        all_resolved = all(
            i.get("resolved_action_id") or i.get("resolved_kind")
            for i in ordered
        )
        intervention_status = "completed" if all_resolved else "waiting_human"

        intervention_map[message_id] = {
            "execution_id": wf_exec.execution_id,
            "status": intervention_status,
            "interventions": [{
                "node_id": i["node_id"],
                "node_name": i.get("node_name", ""),
                "rendered_content": i.get("rendered_content", ""),
                "form_fields": i.get("form_fields", []),
                "actions": i.get("actions", []),
                "timeout_at": _to_ms(i.get("timeout_at")),
                "resolved_action_id": i.get("resolved_action_id"),
                "resolved_form_data": i.get("resolved_form_data"),
                "resolved_at": i.get("resolved_at"),
                "resolved_kind": _public_resolved_kind(i.get("resolved_kind")),
            } for i in ordered],
        }

    conv_dict["pending_intervention"] = intervention_map

    return success(data=conv_dict)


@router.post(
    "/workflow/interventions/{execution_id}/submit",
    summary="提交人工介入响应（分享链接，通知 SSE 流继续执行）",
)
async def submit_shared_human_intervention(
    execution_id: str,
    payload: dict,
    share_data: ShareViewer,
    db: DbSession,
):
    """Forward a human-intervention response to the waiting SSE stream."""

    node_id = payload.get("node_id", "")
    action_id = payload.get("action_id", "")
    form_data = payload.get("form_data")
    if not node_id:
        raise BusinessException("node_id 不能为空", BizCode.BAD_REQUEST)
    if not action_id:
        raise BusinessException("action_id 不能为空", BizCode.BAD_REQUEST)

    share_service = SharedChatService(db)
    share_token = share_data.share_token
    share, release = share_service.get_release_by_share_token(share_token)

    workflow_service = WorkflowService(db)
    execution = workflow_service.get_execution(execution_id)
    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    # Workspace relationship, not app id: the release/app id mapping may differ.
    if execution.app.workspace_id != release.app.workspace_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    if execution.status != "waiting_human":
        raise BusinessException(
            f"当前执行状态为 '{execution.status}'，不接受人工介入响应",
            BizCode.BAD_REQUEST,
        )

    if not submit_intervention(execution_id, node_id, action_id, form_data):
        raise BusinessException(
            "未找到等待中的干预请求，可能 SSE 连接已断开",
            BizCode.BAD_REQUEST,
        )

    return success(data={
        "execution_id": execution_id,
        "node_id": node_id,
        "action_id": action_id,
        "form_data": form_data,
    })


@router.post(
    "/workflow/interventions/{execution_id}/resume-submit",
    summary="恢复 SSE 流并提交人工介入响应（页面刷新后使用）",
)
async def resume_and_submit_intervention(
    execution_id: str,
    payload: dict,
    share_data: ShareViewer,
    db: DbSession,
):
    """Re-establish the interrupted SSE stream and push the viewer's action.

    Used when the original stream was lost (page refresh, connection drop):
    the endpoint re-opens the stream, submits the action, and streams the
    resumed workflow events.
    """

    node_id = payload.get("node_id", "")
    action_id = payload.get("action_id", "")
    form_data = payload.get("form_data")
    if not node_id:
        raise BusinessException("node_id 不能为空", BizCode.BAD_REQUEST)
    if not action_id:
        raise BusinessException("action_id 不能为空", BizCode.BAD_REQUEST)

    share_service = SharedChatService(db)
    share_token = share_data.share_token
    share, release = share_service.get_release_by_share_token(share_token)

    workflow_service = WorkflowService(db)
    execution = workflow_service.get_execution(execution_id)
    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    if execution.app.workspace_id != release.app.workspace_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    intervention_ctx = (execution.context or {}).get("human_intervention", {})
    start_event_data = {
        "conversation_id": intervention_ctx.get("conversation_id"),
        "message_id": intervention_ctx.get("message_id"),
        "execution_id": execution_id,
    }
    # Extracted before the response starts: the ORM row must not be touched
    # once the stream is running.
    execution_app_id = execution.app_id

    async def event_generator():
        start_payload = json.dumps(start_event_data, default=str, ensure_ascii=False)
        yield f"event: start\ndata: {start_payload}\n\n"

        with get_db_context() as stream_db:
            svc = AppChatService(stream_db)
            async for event in svc.workflow_resume_intervention_stream(
                execution_id=execution_id,
                # The execution's own app id: release/app id mapping may differ.
                app_id=execution_app_id,
                node_id=node_id,
                action_id=action_id,
                form_data=form_data,
                public=True,
            ):
                event_type = event.get("event", "message")
                event_data = event.get("data", {})
                yield (
                    f"event: {event_type}\ndata: "
                    f"{json.dumps(event_data, default=str, ensure_ascii=False)}\n\n"
                )

    return sse_response(event_generator())


@router.get(
    "/config",
    summary="获取应用启动配置",
)
async def config_query(
    share_data: ShareViewer,
    db: DbSession,
    password: str | None = Query(None, description="访问密码"),
):
    """Startup config of the released app (variables, memory flag, features)."""

    share_service = SharedChatService(db)
    share_token = share_data.share_token
    share, release = share_service.get_release_by_share_token(share_token, password)
    app_name = release.name
    app_icon = release.icon
    release_config = release.config or {}

    if release.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        workflow_service = WorkflowService(db)
        content = {
            "app_name": app_name,
            "app_icon": app_icon,
            "app_type": release.type,
            "variables": workflow_service.get_start_node_variables(release_config),
            "memory": workflow_service.is_memory_enable(release_config),
            "features": release_config.get("features"),
        }
    elif release.type == AppType.AGENT:
        content = {
            "app_name": app_name,
            "app_icon": app_icon,
            "app_type": release.type,
            "variables": release_config.get("variables"),
            "memory": release_config.get("memory", {}).get("enabled"),
            "features": release_config.get("features"),
            "model_parameters": release_config.get("model_parameters"),
        }
    elif release.type == AppType.MULTI_AGENT:
        supervisor_config = release_config.get("supervisor_config") or {}
        content = {
            "app_name": app_name,
            "app_icon": app_icon,
            "app_type": release.type,
            # Cluster variable definitions live under supervisor_config.variables
            # (same source as the console variable endpoint).
            "variables": supervisor_config.get("variables") or [],
            # supervisor_config.features wins over the top-level fallback.
            "features": supervisor_config.get("features") or release_config.get("features"),
        }
    else:
        return fail(msg="Unsupported app type", code=BizCode.APP_TYPE_NOT_SUPPORTED)
    return success(data=content)


@router.post(
    "/messages/{message_id}/feedback",
    summary="提交消息反馈",
)
async def submit_message_feedback(
    message_id: uuid.UUID,
    payload: app_schema.MessageFeedbackRequest,
    share_data: ShareViewer,
    db: DbSession,
):
    """Like / dislike one assistant reply (idempotent: re-click cancels)."""

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    feedback_service = FeedbackService(db)
    result = feedback_service.submit_feedback(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        user_id=share_data.user_id,
        feedback_type=payload.feedback_type,
        feedback_content=payload.feedback_content,
    )

    return success(data=app_schema.MessageFeedbackResponse(**result))


@router.get(
    "/messages/{message_id}/feedback",
    summary="获取用户对消息的反馈",
)
async def get_user_feedback(
    message_id: uuid.UUID,
    share_data: ShareViewer,
    db: DbSession,
):
    """The current viewer's feedback state of one message."""

    feedback_service = FeedbackService(db)
    result = feedback_service.get_user_feedback(message_id, share_data.user_id)

    return success(data=result)


@router.post(
    "/messages/{message_id}/favorite",
    summary="收藏/取消收藏消息（体验分享场景）",
)
async def toggle_message_favorite(
    message_id: uuid.UUID,
    share_data: ShareViewer,
    db: DbSession,
):
    """Toggle the favorite flag of one message (idempotent)."""

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    favorite_service = FavoriteService(db)
    result = favorite_service.toggle_favorite(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        user_id=share_data.user_id,
    )

    return success(data=app_schema.MessageFavoriteResponse(**result))


@router.post(
    "/messages/{message_id}/report",
    summary="举报消息",
)
async def report_message(
    message_id: uuid.UUID,
    payload: app_schema.MessageReportRequest,
    share_data: ShareViewer,
    db: DbSession,
):
    """Report one message as inappropriate (optionally for a selected span)."""

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=share_data.user_id,
    )

    report_service = ReportService(db)
    result = report_service.submit_report(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        reported_by=end_user.id,
        report_type=payload.report_type,
        report_reason=payload.report_reason,
        text_start_offset=payload.text_start_offset,
        text_end_offset=payload.text_end_offset,
        selected_text=payload.selected_text,
    )

    return success(data=app_schema.MessageReportResponse(**result))


@router.delete(
    "/messages/{message_id}",
    summary="删除消息",
)
async def delete_message(
    message_id: uuid.UUID,
    share_data: ShareViewer,
    db: DbSession,
):
    """Soft-delete one message.

    Deleting an assistant reply drags its whole version group, so later turns
    stop carrying the removed context.
    """

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    conv_service = ConversationService(db)
    conv_service.delete_message(message_id, workspace_id)

    return success(msg="消息已删除")


@router.post(
    "/conversations/{conversation_id}/share",
    summary="分享会话",
)
async def share_conversation(
    conversation_id: uuid.UUID,
    payload: app_schema.ShareConversationRequest,
    share_data: ShareViewer,
    db: DbSession,
):
    """Create a read-only public link to one conversation."""

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user(
        app_id=share.app_id,
        workspace_id=workspace_id,
        other_id=share_data.user_id,
    )

    share_service = ConversationShareService(db)
    result = share_service.create_share(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        user_id=end_user.id,
        password=payload.password,
        expire_hours=payload.expire_hours,
        allow_copy=payload.allow_copy,
    )
    return success(data=app_schema.ShareConversationResponse(**result))


@router.delete(
    "/conversations/share/{share_uuid}",
    summary="撤销分享链接",
)
async def revoke_share(
    share_uuid: str,
    share_data: ShareViewer,
    db: DbSession,
):
    """Revoke one conversation share link."""

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    share_service = ConversationShareService(db)
    share_service.revoke_share(share_uuid, workspace_id)

    return success(msg="分享链接已撤销")


@router.get(
    "/conversations/{conversation_id}/shares",
    summary="列出会话的分享链接",
)
async def list_conversation_shares(
    conversation_id: uuid.UUID,
    share_data: ShareViewer,
    db: DbSession,
):
    """All share links of one conversation."""

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id

    share_service = ConversationShareService(db)
    result = share_service.list_shares(conversation_id, workspace_id)

    return success(data=result)


@router.post(
    "/messages/{message_id}/switch-version/{version}",
    summary="切换消息版本",
)
async def switch_message_version(
    message_id: uuid.UUID,
    version: int,
    share_data: ShareViewer,
    db: DbSession,
):
    """Switch the displayed version of one message."""

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    service = SharedChatService(db)
    share, release = service.get_release_by_share_token(share_data.share_token, None)
    workspace_id = release.app.workspace_id
    conv_service = ConversationService(db)

    result = conv_service.switch_message_version(
        message_id=message_id,
        version=version,
        workspace_id=workspace_id,
    )

    # Backfill the intervention state of the switched-to version so the view
    # can render the HITL UI; agent apps have no workflow execution, the map
    # is empty.
    result["pending_intervention"] = AppLogService(db).build_pending_intervention_map(
        message.conversation_id
    )

    # Node details: same shape as the console
    # /apps/{id}/messages/{mid}/switch-version/{v}/branch route; reuses
    # _build_branch_view's node_executions_map (workflows take the node execution
    # table, agent/cluster apps take agent_executions). Failures degrade to an
    # empty map and never block the version switch itself.
    try:
        branch = WorkflowService(db)._build_branch_view(
            result["message_id"], user_id=str(share_data.user_id)
        )
        result["node_executions_map"] = branch.get("node_executions_map", {})
        # Full conversation chain of the target version's branch: the front end
        # rebuilds the list from it and hides follow-up turns after other versions
        result["messages"] = branch.get("messages", [])
        # Trust the branch view's intervention info (same source as above; only
        # override when non-empty)
        if branch.get("pending_intervention"):
            result["pending_intervention"] = branch["pending_intervention"]
    except Exception as e:
        logger.warning(f"分享端切换版本构建节点明细失败（已降级为空）: {e}")
        result["node_executions_map"] = {}

    return success(data=result)
