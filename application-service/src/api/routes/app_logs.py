"""Application-log routes (frozen port of the monolith ``app_log_controller``).

Conversation list, workflow execution list/detail and the conversation message
detail. The workspace always comes from the principal; all reads run on the
sync session (management plane).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...guards.workspace_access import require_workspace_access
from ...models.app_model import AppType
from ...models.file_metadata_model import FileMetadata
from ...schemas.app_log_schema import (
    AppLogConversation,
    AppLogConversationDetail,
    AppLogMessage,
    LogFileInfo,
    WorkflowExecutionLog,
)
from ...services.app_log_service import AppLogService
from ...services.app_service import AppService
from ...utils.file_urls import build_permanent_file_url
from ..dependencies import get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse, PageData, PageMeta

router = APIRouter(prefix="/apps", tags=["App Logs"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]


@router.get("/{app_id}/logs", summary="应用日志 - 会话列表", response_model=ApiResponse)
def list_app_logs(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
        page: Annotated[int, Query(ge=1)] = 1,
        pagesize: Annotated[int, Query(ge=1, le=100)] = 20,
        is_draft: Annotated[
            bool | None, Query(description="是否草稿会话（不传则返回全部）")
        ] = None,
        keyword: Annotated[str | None, Query(description="搜索关键词（匹配消息内容）")] = None,
        start_date: Annotated[
            datetime | None, Query(description="开始时间（ISO 8601，UTC）")
        ] = None,
        end_date: Annotated[
            datetime | None, Query(description="结束时间（ISO 8601，UTC）")
        ] = None,
):
    """All conversation logs of one app (paginated)

    - is_draft omitted returns all conversations (draft + released)
    - is_draft=True returns draft conversations only
    - is_draft=False returns released conversations only
    - keyword searches message content
    - start_date / end_date filter on conversation creation time (indexed)
    - ordered by last update time, newest first
    """
    # Validate app access
    app = AppService(db).get_app(app_id, workspace_id)

    log_service = AppLogService(db)
    conversations, total = log_service.list_conversations(
        app_id=app_id,
        workspace_id=workspace_id,
        page=page,
        pagesize=pagesize,
        is_draft=is_draft,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
    )

    items = [AppLogConversation.model_validate(c) for c in conversations]

    # Multi-agent clusters: the list page's "sub-agent count" column. Single
    # agent / workflow apps stay at 0 (no query issued, to avoid noise cost).
    if app.type == AppType.MULTI_AGENT and items:
        counts = log_service.count_sub_agents_by_conversations([c.id for c in conversations])
        if counts:
            items = [
                item.model_copy(update={"sub_agent_count": counts.get(str(item.id), 0)})
                for item in items
            ]

    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)

    return success(data=PageData(page=meta, items=items))


@router.get(
    "/{app_id}/workflow-executions",
    summary="应用日志 - 工作流执行列表",
    response_model=ApiResponse,
)
def list_workflow_execution_logs(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
        page: Annotated[int, Query(ge=1)] = 1,
        pagesize: Annotated[int, Query(ge=1, le=100)] = 20,
        is_draft: Annotated[
            bool | None,
            Query(description="是否草稿试运行：true=草稿（release_id 为空），false=已发布版本调用"),
        ] = None,
        start_date: Annotated[
            datetime | None, Query(description="开始时间（ISO 8601，UTC）")
        ] = None,
        end_date: Annotated[
            datetime | None, Query(description="结束时间（ISO 8601，UTC）")
        ] = None,
):
    """Workflow call logs (paginated)

    ``is_draft=false`` returns only calls carrying a release version (published
    entries such as API Key); ``is_draft=true`` returns trial runs only; omitted
    returns both.
    """
    AppService(db).get_app(app_id, workspace_id)

    executions, total = AppLogService(db).list_workflow_executions(
        app_id=app_id,
        page=page,
        pagesize=pagesize,
        is_draft=is_draft,
        start_date=start_date,
        end_date=end_date,
    )
    items = [WorkflowExecutionLog.model_validate(execution) for execution in executions]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)
    return success(data=PageData(page=meta, items=items))


@router.get(
    "/{app_id}/workflow-executions/{execution_id}",
    summary="应用日志 - 工作流执行详情",
    response_model=ApiResponse,
)
def get_workflow_execution_log_detail(
        app_id: UUID,
        execution_id: str,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Single workflow call detail by its business execution_id."""
    AppService(db).get_app(app_id, workspace_id)

    detail = AppLogService(db).get_workflow_execution_log_detail(app_id, execution_id)
    if not detail:
        raise BusinessException("工作流执行记录不存在", BizCode.NOT_FOUND)

    return success(data=detail)


@router.get(
    "/{app_id}/logs/{conversation_id}",
    summary="应用日志 - 会话消息详情",
    response_model=ApiResponse,
)
def get_app_log_detail(
        app_id: UUID,
        conversation_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    """Full message record of one conversation

    - Returns the base conversation info + all messages (ascending time order)
    - Message meta_data carries model name, token usage etc.
    - Everyone (including sharers and grantees) can only view their own
      conversation details
    """
    # Validate app access
    app = AppService(db).get_app(app_id, workspace_id)

    log_service = AppLogService(db)
    conversation, messages, node_executions_map = log_service.get_conversation_detail(
        app_id=app_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        app_type=app.type,
    )

    # Base conversation info (built without ORM relationships)
    base = AppLogConversation.model_validate(conversation)

    # Messages are handled separately to avoid SQLAlchemy relationship checks
    if messages and isinstance(messages[0], AppLogMessage):
        # Workflow: already AppLogMessage instances
        msg_list = messages
    else:
        # Agent: convert ORM Message objects one by one, extracting files
        msg_list = []

        # Collect local file ids and resolve them to accessible URLs in one
        # batch (locally persisted files only store upload_file_id).
        local_file_ids = set()
        for m in messages:
            if isinstance(m.meta_data, dict) and "files" in m.meta_data:
                for f in m.meta_data["files"]:
                    if isinstance(f, dict) and not f.get("url") and f.get("upload_file_id"):
                        try:
                            local_file_ids.add(uuid.UUID(str(f["upload_file_id"])))
                        except (ValueError, TypeError):
                            continue
        local_file_url_map = {}
        if local_file_ids:
            completed_files = db.query(FileMetadata).filter(
                FileMetadata.id.in_(local_file_ids),
                FileMetadata.workspace_id == workspace_id,
                FileMetadata.status == "completed",
            ).all()
            local_file_url_map = {
                str(f.id): build_permanent_file_url(f.id) for f in completed_files
            }

        for m in messages:
            files = []
            resolved_meta_files = []
            if isinstance(m.meta_data, dict) and "files" in m.meta_data:
                for f in m.meta_data["files"]:
                    if not isinstance(f, dict):
                        continue
                    file_url = f.get("url") or local_file_url_map.get(str(f.get("upload_file_id")))
                    if file_url:
                        files.append(LogFileInfo(
                            type=f.get("type", ""),
                            url=file_url,
                            name=f.get("name"),
                            size=f.get("size"),
                            file_type=f.get("file_type"),
                        ))
                        # Inject into the response meta_data.files: the
                        # frontend ChatContent renders directly from it.
                        resolved_meta_files.append({**f, "url": file_url})

            # Copy meta_data so the original ORM object stays untouched.
            response_meta = dict(m.meta_data) if isinstance(m.meta_data, dict) else {}
            if resolved_meta_files:
                response_meta["files"] = resolved_meta_files

            msg_list.append(AppLogMessage(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,
                content=m.content,
                status=m.status,
                meta_data=response_meta,
                files=files,
                created_at=m.created_at,
            ))

    # Aggregate human-intervention info; same shape as the public share
    # endpoint.
    pending_intervention_map = log_service.build_pending_intervention_map(conversation_id)

    # Multi-agent clusters: shallow overview of this round's agents
    # (data-only for now; not rendered by this iteration of the frontend).
    agent_execution_summary = log_service.build_agent_execution_summary(node_executions_map)

    base_data = base.model_dump()
    if app.type == AppType.MULTI_AGENT:
        counts = log_service.count_sub_agents_by_conversations([conversation.id])
        base_data["sub_agent_count"] = counts.get(str(conversation.id), 0)

    detail = AppLogConversationDetail(
        **base_data,
        messages=msg_list,
        node_executions_map=node_executions_map,
        pending_intervention=pending_intervention_map,
        agent_execution_summary=agent_execution_summary,
    )

    return success(data=detail)


__all__ = ["router"]
