"""应用日志（消息记录）接口"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.schemas.app_log_schema import AppLogConversation, AppLogConversationDetail, AppLogMessage, LogFileInfo
from app.schemas.response_schema import PageData, PageMeta
from app.services.app_service import AppService
from app.services.app_log_service import AppLogService

router = APIRouter(prefix="/apps", tags=["App Logs"])
logger = get_business_logger()


@router.get("/{app_id}/logs", summary="应用日志 - 会话列表")
@cur_workspace_access_guard()
def list_app_logs(
        app_id: uuid.UUID,
        page: int = Query(1, ge=1),
        pagesize: int = Query(20, ge=1, le=100),
        is_draft: Optional[bool] = Query(None, description="是否草稿会话（不传则返回全部）"),
        keyword: Optional[str] = Query(None, description="搜索关键词（匹配消息内容）"),
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """查看应用下所有会话记录（分页）

    - is_draft 不传则返回所有会话（草稿 + 正式）
    - is_draft=True 只返回草稿会话
    - is_draft=False 只返回发布会话
    - 支持按 keyword 搜索（匹配消息内容）
    - 按最新更新时间倒序排列
    """
    workspace_id = current_user.current_workspace_id

    # 验证应用访问权限
    app_service = AppService(db)
    app = app_service.get_app(app_id, workspace_id)

    # 使用 Service 层查询
    log_service = AppLogService(db)
    conversations, total = log_service.list_conversations(
        app_id=app_id,
        workspace_id=workspace_id,
        page=page,
        pagesize=pagesize,
        is_draft=is_draft,
        keyword=keyword,
    )

    items = [AppLogConversation.model_validate(c) for c in conversations]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)

    return success(data=PageData(page=meta, items=items))


@router.get("/{app_id}/logs/{conversation_id}", summary="应用日志 - 会话消息详情")
@cur_workspace_access_guard()
def get_app_log_detail(
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """查看某会话的完整消息记录

    - 返回会话基本信息 + 所有消息（按时间正序）
    - 消息 meta_data 包含模型名、token 用量等信息
    - 所有人（包括共享者和被共享者）都只能查看自己的会话详情
    """
    workspace_id = current_user.current_workspace_id

    # 验证应用访问权限
    app_service = AppService(db)
    app = app_service.get_app(app_id, workspace_id)

    # 使用 Service 层查询
    log_service = AppLogService(db)
    conversation, messages, node_executions_map = log_service.get_conversation_detail(
        app_id=app_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        app_type=app.type
    )

    # 构建基础会话信息（不经过 ORM relationship）
    base = AppLogConversation.model_validate(conversation)

    # 单独处理 messages，避免触发 SQLAlchemy relationship 校验
    if messages and isinstance(messages[0], AppLogMessage):
        # 工作流：已经是 AppLogMessage 实例
        msg_list = messages
    else:
        # Agent：ORM Message 对象逐个转换，提取 files
        msg_list = []
        for m in messages:
            files = []
            if isinstance(m.meta_data, dict) and "files" in m.meta_data:
                for f in m.meta_data["files"]:
                    if isinstance(f, dict) and f.get("url"):
                        files.append(LogFileInfo(
                            type=f.get("type", ""),
                            url=f["url"],
                            name=f.get("name"),
                            size=f.get("size"),
                            file_type=f.get("file_type"),
                        ))
            msg_list.append(AppLogMessage(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,
                content=m.content,
                status=m.status,
                meta_data=m.meta_data,
                files=files,
                created_at=m.created_at,
            ))

    detail = AppLogConversationDetail(
        **base.model_dump(),
        messages=msg_list,
        node_executions_map=node_executions_map,
    )

    return success(data=detail)
