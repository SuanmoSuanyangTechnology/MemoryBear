"""Message-interaction routes (frozen port of the monolith
``message_interaction_controller`` and the ``/v1`` app subset of
``service/app_api_controller`` + ``service/message_feedback_api_controller``).

Two faces live in this module: the manager face (``/apps``, workspace-guarded;
the report-type enum carries no guard on the host and stays unguarded here) and
the ``/v1`` API-key face (``/app``); the gateway rewrites both onto
``/internal/v1``. On the ``/v1`` face the gateway terminates the key but
forwards neither the key's resource binding nor the monolith's self-db
pipeline, so the service resolves the binding from the injected key id (shared
helper) and re-applies the publish-state and scope checks; the host
``_get_app_id`` app-binding check is re-applied on the routes that carry it.
Handlers ride either session: message management is synchronous on the
request-scoped sync session; the chat face (``/v1`` chat and file upload) and
reply regeneration are async — the chat key gate and resolution run on short
async sessions, each stream opens its own session leg, so no session stays
checked out for the lifetime of a streaming response; regeneration runs on
sync-db services through their own sync session context.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from ...db import get_async_db_context, get_db_context
from ...errors import BizCode, BusinessException, ResourceNotFoundException
from ...guards.workspace_access import require_workspace_access
from ...models import (
    AppRelease,
    AppType,
    Message,
    MessageReportType,
    ModelConfig,
    Workspace,
)
from ...schemas.app_schema import (
    AppChatRequest,
    MessageFavoriteResponse,
    MessageFeedbackRequest,
    MessageFeedbackResponse,
    MessageReportRequest,
    MessageReportResponse,
    MessageVersion,
    RegenerateRequest,
    RegenerateResponse,
    ReportReviewRequest,
    WorkflowRegenerateResponse,
)
from ...schemas.conversation_schema import (
    V1ConversationFeedbackListResponse,
    V1ConversationListItem,
    V1ConversationMessageListResponse,
)
from ...services.agent_config_helper import enrich_agent_config
from ...services.app_chat_service import assert_not_opening_statement
from ...services.app_service import AppService
from ...services.chat_use_case import (
    chat_complete,
    chat_stream,
    prepare_chat,
    resolve_app_for_chat,
)
from ...services.chat_use_case import (
    require_bound_app_id as _require_app_id,
)
from ...services.conversation_service import ConversationService
from ...services.draft_run_service import AgentRunService
from ...services.file_storage_service import (
    FileStorageService,
    get_file_storage_service,
    upload_workspace_file,
)
from ...services.message_feedback_service import FavoriteService, FeedbackService
from ...services.message_report_service import ReportService
from ...services.multi_agent_service import MultiAgentService
from ...services.workflow_service import WorkflowService
from ...utils.app_config_utils import agent_config_4_app_release
from ...utils.datetime_utils import to_timestamp_ms
from ...utils.sse_stream import sse_response
from ..dependencies import API_KEY_ID_HEADER, Principal, get_principal, get_sync_db
from ..schemas.common import fail, success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta
from starlette.responses import JSONResponse
from .annotations import _resolve_v1_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["Message Interaction"])
v1_router = APIRouter(prefix="/app", tags=["Message Interaction API (v1)"])

DbSession = Annotated[Session, Depends(get_sync_db)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]
Caller = Annotated[Principal, Depends(get_principal)]


# ==================== Manager face ====================


@router.post("/{app_id}/messages/{message_id}/regenerate", summary="重新生成回复",
             response_model=ApiResponse)
async def regenerate_message(
    app_id: UUID,
    message_id: UUID,
    payload: RegenerateRequest,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Regenerate an assistant reply (multi-version, streaming or not).

    Routed by app type: WORKFLOW reruns the flow through the restored input
    (conversational flows only), AGENT keeps the same context and re-invokes
    the LLM, PURE_WORKFLOW is one-shot and rejected. Every validation runs
    before the SSE response starts, so failures still return clean HTTP
    errors instead of a blank stream.
    """

    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)

    # Must run before the StreamingResponse: an opening statement has no parent
    # user message to restore, and a failure after the stream started cannot
    # become a clean HTTP error anymore.
    assert_not_opening_statement(db, message_id)

    if app.type == AppType.PURE_WORKFLOW:
        raise BusinessException(
            "纯工作流为一次性执行，不支持重新生成", BizCode.APP_TYPE_NOT_SUPPORTED
        )

    if app.type == AppType.WORKFLOW:
        workflow_service = WorkflowService(db)
        config = workflow_service._resolve_workflow_config(app, workspace_id)
        # Only conversational flows carry the multi-turn semantics a
        # single-round regeneration needs.
        if not workflow_service._supports_conversation(config):
            raise BusinessException("该工作流非对话流，不支持重新生成", BizCode.BAD_REQUEST)

        workflow_service._assert_regenerate_quota(message_id)

        if payload.stream:
            async def event_generator():
                with get_db_context() as stream_db:
                    svc = WorkflowService(stream_db)
                    async for event in svc.regenerate_stream(
                            message_id=message_id,
                            app_id=app_id,
                            config=config,
                            workspace_id=workspace_id,
                            user_id=str(principal.actor_id),
                            override_variables=payload.variables,
                    ):
                        event_type = event.get("event", "message")
                        event_data = event.get("data", {})
                        yield (
                            f"event: {event_type}\ndata: "
                            f"{json.dumps(event_data, default=str, ensure_ascii=False)}\n\n"
                        )

            return sse_response(event_generator())

        result = await workflow_service.regenerate(
            message_id=message_id,
            app_id=app_id,
            config=config,
            workspace_id=workspace_id,
            user_id=str(principal.actor_id),
            override_variables=payload.variables,
        )
        return success(data=WorkflowRegenerateResponse(**result))

    # ---- multi-agent cluster branch (MULTI_AGENT) ----
    # Reruns through the orchestrator (keeping supervisor routing / sub-agent
    # orchestration); version management aligns with Agent apps. Cluster messages
    # previously fell into the single-agent engine branch, which lost the
    # orchestration semantics on rerun.
    if app.type == AppType.MULTI_AGENT:
        if payload.stream:
            async def cluster_event_generator():
                with get_db_context() as stream_db:
                    svc = MultiAgentService(stream_db)
                    async for event in svc.regenerate_stream(
                            message_id=message_id,
                            app_id=app_id,
                            user_id=str(principal.actor_id),
                            variables=payload.variables,
                            web_search=payload.web_search,
                            memory=payload.memory,
                            # Same as Agent: thinking is decided solely by the
                            # config's deep_thinking flag
                            thinking=True,
                    ):
                        yield event

            return sse_response(cluster_event_generator())

        result = await MultiAgentService(db).regenerate(
            message_id=message_id,
            app_id=app_id,
            user_id=str(principal.actor_id),
            variables=payload.variables,
            web_search=payload.web_search,
            memory=payload.memory,
            thinking=True,
        )
        return success(data=RegenerateResponse(**result))

    # ---- agent branch (the host routes every non-workflow type here; shared
    # config resolution rejects non-agent types with APP_TYPE_NOT_SUPPORTED) ----
    agent_cfg = app_service.get_agent_config(app_id=app_id, workspace_id=workspace_id)

    # In a multi-model compare trial run each column may use a different model
    # with different model_parameters; those are persisted on the original
    # assistant message's meta_data. Regenerate must restore the model/parameters
    # that actually produced the message instead of the app default/global ones.
    target_msg = db.get(Message, message_id)
    msg_meta = dict(getattr(target_msg, "meta_data", None) or {})
    meta_model_config_id = msg_meta.get("model_config_id")
    model_parameters_override = msg_meta.get("model_parameters_override")

    model_config = None
    if meta_model_config_id:
        try:
            model_config = db.get(ModelConfig, UUID(str(meta_model_config_id)))
        except (ValueError, TypeError):
            model_config = None
    if not model_config and agent_cfg.default_model_config_id:
        model_config = db.get(ModelConfig, agent_cfg.default_model_config_id)

    if not model_config:
        raise ResourceNotFoundException("模型配置", str(agent_cfg.default_model_config_id))

    if payload.stream:
        async def event_generator():
            with get_db_context() as stream_db:
                svc = AgentRunService(stream_db)
                async for event in svc.regenerate_stream(
                        message_id=message_id,
                        agent_config=agent_cfg,
                        model_config=model_config,
                        workspace_id=workspace_id,
                        user_id=str(principal.actor_id),
                        model_parameters_override=model_parameters_override,
                ):
                    yield event

        return sse_response(event_generator())

    result = await AgentRunService(db).regenerate(
        message_id=message_id,
        agent_config=agent_cfg,
        model_config=model_config,
        workspace_id=workspace_id,
        user_id=str(principal.actor_id),
        model_parameters_override=model_parameters_override,
    )
    return success(data=RegenerateResponse(**result))


@router.post(
    "/{app_id}/messages/{message_id}/switch-version/{version}/branch",
    summary="切换版本并返回该分支完整对话链（含节点明细）",
    response_model=ApiResponse,
)
def switch_message_version_with_branch(
    app_id: UUID,
    message_id: UUID,
    version: int,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Switch the displayed version, then return the full branch view anchored
    on the target version id (host merge of switch + branch chain).

    The anchor is the switch result, so a sibling id passed in still resolves
    to the switched branch.
    """

    result = ConversationService(db).switch_message_version(message_id, version, workspace_id)
    branch = WorkflowService(db)._build_branch_view(
        result["message_id"], user_id=str(principal.actor_id)
    )
    return success(data=branch)


@router.get("/{app_id}/messages/{message_id}/versions", summary="获取消息的所有版本",
            response_model=ApiResponse)
def list_message_versions(
    app_id: UUID,
    message_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """List every regenerated version of one message."""

    versions = ConversationService(db).get_message_versions(message_id)
    return success(data=[MessageVersion(**v) for v in versions])


@router.post("/{app_id}/messages/{message_id}/feedback", summary="提交消息反馈",
             response_model=ApiResponse)
def submit_message_feedback(
    app_id: UUID,
    message_id: UUID,
    payload: MessageFeedbackRequest,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Submit like / dislike feedback for one message (idempotent toggle)."""

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    result = FeedbackService(db).submit_feedback(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        user_id=str(principal.actor_id),
        feedback_type=payload.feedback_type,
        feedback_content=payload.feedback_content,
    )
    return success(data=MessageFeedbackResponse(**result))


@router.post("/{app_id}/messages/{message_id}/favorite", summary="收藏/取消收藏消息",
             response_model=ApiResponse)
def toggle_message_favorite(
    app_id: UUID,
    message_id: UUID,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Toggle the favorite flag of one message (idempotent)."""

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    result = FavoriteService(db).toggle_favorite(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        user_id=str(principal.actor_id),
    )
    return success(data=MessageFavoriteResponse(**result))


@router.get("/{app_id}/messages/{message_id}/feedback", summary="获取用户对消息的反馈",
            response_model=ApiResponse)
def get_user_feedback(
    app_id: UUID,
    message_id: UUID,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Current user's feedback of one message (empty data when absent)."""

    result = FeedbackService(db).get_user_feedback(message_id, str(principal.actor_id))
    return success(data=result)


@router.post("/{app_id}/messages/{message_id}/report", summary="举报消息",
             response_model=ApiResponse)
def report_message(
    app_id: UUID,
    message_id: UUID,
    payload: MessageReportRequest,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Report one message, optionally with the selected text span."""

    message = db.get(Message, message_id)
    if not message:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    result = ReportService(db).submit_report(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        reported_by=principal.actor_id,
        report_type=payload.report_type,
        report_reason=payload.report_reason,
        text_start_offset=payload.text_start_offset,
        text_end_offset=payload.text_end_offset,
        selected_text=payload.selected_text,
    )
    return success(data=MessageReportResponse(**result))


@router.get("/enums/message_report_types", summary="获取举报类型枚举", response_model=ApiResponse)
def get_message_report_types():
    """Report-type enum; the host route carries no guard and stays unguarded."""

    return success(data=MessageReportType.get_all_types_with_labels())


@router.delete("/{app_id}/messages/{message_id}", summary="删除消息", response_model=ApiResponse)
def delete_message(
    app_id: UUID,
    message_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """Soft-delete one message (an assistant reply drags down its version group)."""

    ConversationService(db).delete_message(message_id, workspace_id)
    return success(msg="消息已删除")


# ==================== Report moderation (operations) ====================


@router.get("/messages/reports", summary="获取举报列表（待审核）", response_model=ApiResponse)
def get_reports_for_review(
    workspace_id: WorkspaceId,
    db: DbSession,
    status: str | None = None,
    severity: str | None = None,
    page: int = 1,
    pagesize: int = 20,
):
    """Filtered + paginated report queue, newest first."""

    reports, total = ReportService(db).get_reports_for_review(
        workspace_id=workspace_id,
        status=status,
        severity=severity,
        page=page,
        pagesize=pagesize,
    )
    items = [
        {
            "id": str(r.id),
            "message_id": str(r.message_id),
            "conversation_id": str(r.conversation_id),
            "report_type": r.report_type,
            "report_reason": r.report_reason,
            "severity": r.severity,
            "status": r.status,
            "selected_text": r.selected_text,
            "text_start_offset": r.text_start_offset,
            "text_end_offset": r.text_end_offset,
            "created_at": to_timestamp_ms(r.created_at),
        }
        for r in reports
    ]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items))


@router.post("/reports/{report_id}/review", summary="审核举报", response_model=ApiResponse)
def review_report(
    report_id: UUID,
    payload: ReportReviewRequest,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Review one report; the chosen action drives its side effects."""

    result = ReportService(db).review_report(
        report_id=report_id,
        reviewer_id=principal.actor_id,
        severity=payload.severity,
        action_taken=payload.action_taken,
        review_note=payload.review_note,
    )
    return success(data=result)


@router.get("/messages/reports/statistics", summary="获取举报统计", response_model=ApiResponse)
def get_reports_statistics(
    workspace_id: WorkspaceId,
    db: DbSession,
    start_date: int | None = None,
    end_date: int | None = None,
):
    """Grouped report statistics with an optional unix time range.

    Values above 1e12 are treated as milliseconds, otherwise seconds (host
    parse semantics).
    """

    def parse_datetime(value: int | None):
        if not value:
            return None
        try:
            if value > 1e12:
                return datetime.fromtimestamp(value / 1000)
            return datetime.fromtimestamp(value)
        except (ValueError, TypeError):
            return None

    result = ReportService(db).get_reports_statistics(
        workspace_id=workspace_id,
        start_date=parse_datetime(start_date),
        end_date=parse_datetime(end_date),
    )
    return success(data=result)


# ==================== /v1 API-key face ====================


async def _read_json_body(request: Request) -> dict:
    """Parse the raw JSON body; malformed input becomes a parameter error.

    Endpoints parsing the body manually would otherwise let invalid JSON fall
    through as an uncaught 500; this converts it to the same 1003 / HTTP 400
    business error the host helper raises.
    """

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise BusinessException(
            "请求体不是合法的 JSON", BizCode.INVALID_PARAMETER, cause=exc
        ) from exc
    if not isinstance(body, dict):
        raise BusinessException("请求体必须是 JSON 对象", BizCode.INVALID_PARAMETER)
    return body


class ChatRequestValidationError(Exception):
    """/v1/app/chat 请求体框架校验失败：携带对外展示的 detail。"""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def chat_validation_content(detail: str) -> dict:
    """V1 chat 框架请求校验失败统一信封内容：HTTP 422 / code 1001（文档 1.2）。"""
    return fail(code=BizCode.VALIDATION_FAILED.value, msg=detail, error=detail)


def _validation_failed_response(detail: str) -> JSONResponse:
    """V1 chat 框架请求校验失败统一信封：HTTP 422 / code 1001（文档 1.2）。"""
    return JSONResponse(status_code=422, content=chat_validation_content(detail))


def validate_chat_body(body: object) -> AppChatRequest:
    """已解码 JSON body → AppChatRequest；非法结构统一 422/1001（HTTP 与 gRPC 共用）。"""
    if not isinstance(body, dict):
        raise ChatRequestValidationError("请求体必须是 JSON 对象")
    try:
        return AppChatRequest(**body)
    except PydanticValidationError as exc:
        # 文档 1.2：仅 message 类型错误走标准 fail 信封（422/1001）；
        # conversation_id 非法 UUID 等其余字段校验失败保持全局
        # PydanticValidationError 处理器的 VALIDATION_FAILED 信封
        if any("message" in err.get("loc", ()) for err in exc.errors()):
            raise ChatRequestValidationError("数据验证失败")
        raise


async def _parse_chat_payload(request: Request) -> AppChatRequest:
    """解析 /v1/app/chat 请求体，非法 JSON / 非 object / 字段类型错误统一为 422/1001。"""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise ChatRequestValidationError("请求体不是合法的 JSON")
    return validate_chat_body(body)


def _parse_release_config(release: AppRelease) -> dict:
    """Release config snapshot as a dict (JSON-string and dirty values tolerated)."""

    config = release.config or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            config = {}
    return config if isinstance(config, dict) else {}


def _get_standard_variables(variables: list, app_type: AppType) -> list:
    """Normalize Agent / Workflow variable definitions to one output shape."""

    is_agent = app_type in (AppType.AGENT, AppType.MULTI_AGENT)
    result = []
    for raw in variables:
        v = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
        if is_agent:
            ui_type = v.get("type", "string")
            data_type = "number" if ui_type == "number" else "string"
        else:
            ui_type = v.get("ui_type") or ("number" if v.get("type") == "number" else "text-input")
            data_type = v.get("type", "string")
        result.append({
            "name": v["name"],
            "display_name": v.get("display_name"),
            "type": data_type,
            "ui_type": ui_type,
            "required": v.get("required", False),
            "description": v.get("description"),
            "max_length": v.get("max_length"),
            "default": None if is_agent else v.get("default"),
            "options": None if is_agent else v.get("options"),
            "allowed_file_types": None if is_agent else v.get("allowed_file_types"),
            "max_file_count": None if is_agent else v.get("max_file_count"),
            "max_file_size_mb": None if is_agent else v.get("max_file_size_mb"),
        })
    return result


def _variables_from_release(release: AppRelease) -> list:
    """Variable definitions of the current release snapshot, by app type."""

    config = _parse_release_config(release)
    if release.type == AppType.AGENT:
        cfg = agent_config_4_app_release(release)
        cfg = enrich_agent_config(cfg)
        variables = cfg.variables or config.get("variables") or []
    elif release.type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        variables = WorkflowService.get_start_node_variables(config)
    elif release.type == AppType.MULTI_AGENT:
        supervisor_cfg = config.get("supervisor_config")
        variables = (supervisor_cfg or {}).get("variables") or []
    else:
        raise BusinessException(
            f"不支持的应用类型: {release.type}", BizCode.APP_TYPE_NOT_SUPPORTED
        )
    return _get_standard_variables(variables, release.type)


@v1_router.get("", summary="列出可访问的应用（占位）", response_model=ApiResponse)
def v1_list_apps():
    """Placeholder root kept from the host."""

    return success(data=[], msg="App API - Coming Soon")


@v1_router.get("/variable", summary="获取应用变量配置", response_model=ApiResponse)
def v1_get_app_variables(
    request: Request,
    principal: Caller,
    db: DbSession,
):
    """Variable definitions of the key's bound app (current release snapshot)."""

    app_id = _require_app_id(_resolve_v1_app(request, db, principal))
    workspace_id = principal.workspace_id

    app_service = AppService(db)
    app_service.get_app(app_id, workspace_id)
    release = app_service.get_current_release(app_id=app_id, workspace_id=workspace_id)
    if not release:
        raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

    return success(data=_variables_from_release(release))


@v1_router.get("/conversations", summary="获取会话列表", response_model=ApiResponse)
def v1_list_conversations(
    request: Request,
    principal: Caller,
    db: DbSession,
    user_id: str = Query("", description="外部系统用户 ID"),
    page: int = Query(1, description="页码，从 1 开始"),
    page_size: int = Query(20, description="每页数量，最大 100"),
):
    """Conversation list of one external end-user under the key's bound app."""

    app_id = _resolve_v1_app(request, db, principal)

    result = ConversationService(db).list_v1_conversations(
        app_id=app_id,
        workspace_id=principal.workspace_id,
        external_user_id=user_id,
        page=page,
        page_size=page_size,
    )
    items = [V1ConversationListItem(**item) for item in result["items"]]
    page_meta = PageMeta(
        page=result["page"],
        pagesize=result["page_size"],
        total=result["total"],
        hasnext=result["hasnext"],
    )
    return success(data=PageData(page=page_meta, items=items).model_dump(mode="json"))


@v1_router.get("/conversations/{conversation_id}/messages", summary="获取会话历史消息",
               response_model=ApiResponse)
def v1_list_conversation_messages(
    request: Request,
    conversation_id: UUID,
    principal: Caller,
    db: DbSession,
    user_id: str = Query("", description="外部系统用户 ID"),
    limit: int = Query(20, description="返回消息数量，最大 200"),
):
    """History messages of one conversation owned by the external end-user."""

    app_id = _resolve_v1_app(request, db, principal)

    result = ConversationService(db).list_v1_conversation_messages(
        app_id=app_id,
        workspace_id=principal.workspace_id,
        external_user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
    )
    return success(data=V1ConversationMessageListResponse(**result).model_dump(mode="json"))


@v1_router.post("/messages/{message_id}/feedback", summary="提交消息反馈（点赞/点踩）",
                response_model=ApiResponse)
def v1_submit_message_feedback(
    request: Request,
    message_id: UUID,
    principal: Caller,
    db: DbSession,
    body: Annotated[dict, Depends(_read_json_body)],
    user_id: str = Query(..., description="外部系统用户 ID（other_id）"),
):
    """Submit like / dislike feedback for one message (idempotent toggle)."""

    payload = MessageFeedbackRequest(**body)

    result = ConversationService(db).submit_v1_message_feedback(
        app_id=_resolve_v1_app(request, db, principal),
        workspace_id=principal.workspace_id,
        external_user_id=user_id,
        message_id=message_id,
        feedback_type=payload.feedback_type,
        feedback_content=payload.feedback_content,
    )
    return success(data=MessageFeedbackResponse(**result).model_dump(mode="json"))


@v1_router.get("/conversations/{conversation_id}/messages/feedbacks",
               summary="获取会话下消息的点赞与反馈", response_model=ApiResponse)
def v1_get_conversation_feedback(
    request: Request,
    conversation_id: UUID,
    principal: Caller,
    db: DbSession,
    user_id: str = Query(..., description="外部系统用户 ID（other_id）"),
    limit: int = Query(50, ge=1, le=200, description="返回消息数量，最大 200"),
):
    """Feedback states of one conversation for the external end-user (no favorite)."""

    result = ConversationService(db).list_v1_conversation_feedback(
        app_id=_resolve_v1_app(request, db, principal),
        workspace_id=principal.workspace_id,
        external_user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
    )
    return success(data=V1ConversationFeedbackListResponse(**result).model_dump(mode="json"))


@v1_router.get("/messages/{message_id}/suggested", summary="获取消息预制问题",
               response_model=ApiResponse)
def v1_get_message_suggested_questions(
    request: Request,
    message_id: UUID,
    principal: Caller,
    db: DbSession,
):
    """Preset questions of one assistant message (meta_data.suggested_questions)."""

    app_id = _require_app_id(_resolve_v1_app(request, db, principal))

    questions = ConversationService(db).get_v1_message_suggested_questions(
        app_id=app_id,
        workspace_id=principal.workspace_id,
        message_id=message_id,
    )
    return success(data=questions)


@v1_router.get("/info", summary="获取应用基本信息", response_model=ApiResponse)
def v1_get_app_basic_info(
    request: Request,
    principal: Caller,
    db: DbSession,
    version: Annotated[
        UUID | None, Query(description="发布版本 ID，不传则使用当前生效版本")
    ] = None,
):
    """Basic info of the key's bound app (from a release snapshot)."""

    app_id = _require_app_id(_resolve_v1_app(request, db, principal))
    workspace_id = principal.workspace_id

    app_service = AppService(db)
    app_service.get_app(app_id, workspace_id)
    if version is not None:
        release = app_service.get_release_by_id(app_id=app_id, release_id=version)
    else:
        release = app_service.get_current_release(app_id=app_id, workspace_id=workspace_id)
        if not release:
            raise BusinessException("应用未发布，不可用", BizCode.APP_NOT_PUBLISHED)

    return success(data={
        "app_id": str(release.app_id),
        "name": release.name,
        "description": release.description,
        "icon": release.icon,
        "type": release.type,
    })


@v1_router.post("/chat", summary="发送消息（支持流式和非流式）")
async def v1_chat(
    request: Request,
    principal: Caller,
):
    """Chat with the key's bound app (agent / multi-agent / workflow).

    App, release, end-user and memory-context resolution share one async
    session; the execution legs open their own sessions. Every validation
    finishes before a streaming response starts so failures still return
    clean HTTP errors. Sandbox execution is host-only: the service always
    runs in-process.
    """

    # Auth gate first (host decorator order): the key binding, publish state
    # and scope are re-applied before the body is even read. The gate rides a
    # short async session — a request-scoped sync session here would stay
    # checked out until the SSE stream ends and starve the sync pool.
    # Preparation and both execution legs live in the shared chat use case
    # (the gRPC servicer runs the same pipeline).
    app_id = await resolve_app_for_chat(
        api_key_id=request.headers.get(API_KEY_ID_HEADER),
        workspace_id=principal.workspace_id,
    )
    workspace_id = principal.workspace_id

    try:
        payload = await _parse_chat_payload(request)
    except ChatRequestValidationError as exc:
        return _validation_failed_response(exc.detail)

    prep = await prepare_chat(app_id=app_id, workspace_id=workspace_id, payload=payload)
    if payload.stream:
        return sse_response(chat_stream(prep))
    return await chat_complete(prep)


@v1_router.post("/files", summary="AI 对话文件上传", response_model=ApiResponse)
async def v1_upload_chat_file(
    request: Request,
    principal: Caller,
    file: Annotated[UploadFile, File()],
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
):
    """Upload one file for multimodal chat, referenced later through the
    ``/chat`` request's ``files`` field."""

    app_id = await resolve_app_for_chat(
        api_key_id=request.headers.get(API_KEY_ID_HEADER),
        workspace_id=principal.workspace_id,
    )

    async with get_async_db_context() as adb:
        workspace = await adb.get(Workspace, principal.workspace_id)
        if workspace is None:
            raise BusinessException("Workspace not found", BizCode.NOT_FOUND)
        tenant_id = workspace.tenant_id

    logger.info(
        f"V1 app file upload: app_id={app_id}, "
        f"workspace_id={principal.workspace_id}, filename={file.filename}"
    )

    try:
        upload_result = await upload_workspace_file(
            tenant_id=tenant_id,
            workspace_id=principal.workspace_id,
            file=file,
            storage_service=storage_service,
        )
    except HTTPException as exc:
        logger.error(f"Storage upload failed: {exc}")
        raise

    logger.info(f"File uploaded to storage: file_key={upload_result['file_key']}")
    return success(data=upload_result, msg="File upload successful")


__all__ = ["router", "v1_router"]
