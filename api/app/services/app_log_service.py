"""应用日志服务层"""
import uuid
import datetime as dt
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.models.app_model import AppType
from app.models.conversation_model import Conversation, Message
from app.models.workflow_model import WorkflowExecution
from app.repositories.conversation_repository import ConversationRepository, MessageRepository
from app.schemas.app_log_schema import AppLogMessage, AppLogNodeExecution

logger = get_business_logger()


class AppLogService:
    """应用日志服务"""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_repository = ConversationRepository(db)
        self.message_repository = MessageRepository(db)

    def list_conversations(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        is_draft: Optional[bool] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[list[Conversation], int]:
        """
        查询应用日志会话列表

        Args:
            app_id: 应用 ID
            workspace_id: 工作空间 ID
            page: 页码（从 1 开始）
            pagesize: 每页数量
            is_draft: 是否草稿会话（None表示返回全部）
            keyword: 搜索关键词（匹配消息内容）

        Returns:
            Tuple[list[Conversation], int]: (会话列表，总数)
        """
        logger.info(
            "查询应用日志会话列表",
            extra={
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "page": page,
                "pagesize": pagesize,
                "is_draft": is_draft,
                "keyword": keyword
            }
        )

        # 使用 Repository 查询
        conversations, total = self.conversation_repository.list_app_conversations(
            app_id=app_id,
            workspace_id=workspace_id,
            is_draft=is_draft,
            keyword=keyword,
            page=page,
            pagesize=pagesize
        )

        logger.info(
            "查询应用日志会话列表成功",
            extra={
                "app_id": str(app_id),
                "total": total,
                "returned": len(conversations)
            }
        )

        return conversations, total

    def get_conversation_detail(
        self,
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        app_type: str = AppType.AGENT
    ) -> Tuple[Conversation, list, dict[str, list[AppLogNodeExecution]]]:
        """
        查询会话详情

        Returns:
            Tuple[Conversation, list[AppLogMessage|Message], dict[str, list[AppLogNodeExecution]]]
        """
        logger.info(
            "查询应用日志会话详情",
            extra={
                "app_id": str(app_id),
                "conversation_id": str(conversation_id),
                "workspace_id": str(workspace_id),
                "app_type": app_type
            }
        )

        conversation = self.conversation_repository.get_conversation_for_app_log(
            conversation_id=conversation_id,
            app_id=app_id,
            workspace_id=workspace_id
        )

        if app_type == AppType.WORKFLOW:
            messages, node_executions_map = self._get_workflow_messages_and_nodes(conversation_id)
        else:
            messages = self.message_repository.get_messages_by_conversation(
                conversation_id=conversation_id
            )
            _, node_executions_map = self._get_workflow_node_executions_with_map(
                conversation_id, messages
            )

        logger.info(
            "查询应用日志会话详情成功",
            extra={
                "app_id": str(app_id),
                "conversation_id": str(conversation_id),
                "message_count": len(messages),
                "message_with_nodes_count": len(node_executions_map)
            }
        )

        return conversation, messages, node_executions_map

    def _get_workflow_messages_and_nodes(
        self,
        conversation_id: uuid.UUID,
    ) -> Tuple[list[AppLogMessage], dict[str, list[AppLogNodeExecution]]]:
        """
        工作流应用专用：从 workflow_executions 构建 messages 和节点日志。

        每条 WorkflowExecution 对应一轮对话：
          - user message：来自 execution.input_data
          - assistant message：来自 execution.output_data（失败时内容为错误信息）
        节点日志以 execution id 为 key 分组。

        Returns:
            (messages 列表, node_executions_map)
        """
        stmt = (
            select(WorkflowExecution)
            .where(
                WorkflowExecution.conversation_id == conversation_id,
                WorkflowExecution.status.in_(["completed", "failed"])
            )
            .order_by(WorkflowExecution.started_at.asc())
        )
        executions = list(self.db.scalars(stmt).all())

        messages: list[AppLogMessage] = []
        node_executions_map: dict[str, list[AppLogNodeExecution]] = {}

        for execution in executions:
            started_at = execution.started_at or dt.datetime.now()
            completed_at = execution.completed_at or started_at

            # assistant message 的 id，同时作为 node_executions_map 的 key
            assistant_msg_id = uuid.uuid5(execution.id, "assistant")

            # --- user message（输入）---
            input_content = _extract_text(execution.input_data)
            user_msg = AppLogMessage(
                id=uuid.uuid5(execution.id, "user"),
                conversation_id=conversation_id,
                role="user",
                content=input_content,
                meta_data=None,
                created_at=started_at,
            )
            messages.append(user_msg)

            # --- assistant message（输出）---
            if execution.status == "completed":
                output_content = _extract_text(execution.output_data)
                meta = {"usage": execution.token_usage or {}, "elapsed_time": execution.elapsed_time}
            else:
                output_content = _extract_text(execution.output_data) or ""
                meta = {"error": execution.error_message, "error_node_id": execution.error_node_id}

            assistant_msg = AppLogMessage(
                id=assistant_msg_id,
                conversation_id=conversation_id,
                role="assistant",
                content=output_content,
                status=execution.status,
                meta_data=meta,
                created_at=completed_at,
            )
            messages.append(assistant_msg)

            # --- 节点执行记录，key 与 assistant message id 一致 ---
            execution_nodes = []
            for node_exec in execution.node_executions:
                output_data = dict(node_exec.output_data or {})
                cycle_items = output_data.pop("cycle_items", None)
                execution_nodes.append(AppLogNodeExecution(
                    node_id=node_exec.node_id,
                    node_type=node_exec.node_type,
                    node_name=node_exec.node_name,
                    status=node_exec.status,
                    error=node_exec.error_message,
                    input=node_exec.input_data,
                    process=None,
                    output=output_data,
                    cycle_items=cycle_items,
                    elapsed_time=node_exec.elapsed_time,
                    token_usage=node_exec.token_usage,
                ))

            if execution_nodes:
                node_executions_map[str(assistant_msg_id)] = execution_nodes

        return messages, node_executions_map

    def _get_workflow_node_executions_with_map(
        self,
        conversation_id: uuid.UUID,
        messages: list[Message]
    ) -> Tuple[list[AppLogNodeExecution], dict[str, list[AppLogNodeExecution]]]:
        """
        从 workflow_executions 表中提取节点执行记录，并按 assistant message 分组

        Args:
            conversation_id: 会话 ID
            messages: 消息列表

        Returns:
            Tuple[list[AppLogNodeExecution], dict[str, list[AppLogNodeExecution]]]:
                (所有节点执行记录列表, 按 message_id 分组的节点执行记录字典)
        """
        node_executions = []
        node_executions_map: dict[str, list[AppLogNodeExecution]] = {}

        # 查询该会话关联的所有工作流执行记录（按时间正序）
        stmt = select(WorkflowExecution).where(
            WorkflowExecution.conversation_id == conversation_id,
            WorkflowExecution.status.in_(["completed", "failed"])
        ).order_by(WorkflowExecution.started_at.asc())

        executions = self.db.scalars(stmt).all()

        logger.info(
            f"查询到 {len(executions)} 条工作流执行记录",
            extra={
                "conversation_id": str(conversation_id),
                "execution_count": len(executions),
                "execution_ids": [str(e.id) for e in executions]
            }
        )

        # 筛选出 workflow 执行产生的 assistant 消息（排除开场白）
        # workflow 结果的 meta_data 包含 usage，而开场白包含 suggested_questions
        assistant_messages = [
            m for m in messages
            if m.role == "assistant" and m.meta_data and "usage" in m.meta_data
        ]

        # 通过时序匹配，将 execution 和 assistant message 关联
        used_message_ids: set[str] = set()

        for execution in executions:
            # 构建节点执行记录列表
            execution_nodes = []
            for node_exec in execution.node_executions:
                output_data = dict(node_exec.output_data or {})
                cycle_items = output_data.pop("cycle_items", None)
                node_execution = AppLogNodeExecution(
                    node_id=node_exec.node_id,
                    node_type=node_exec.node_type,
                    node_name=node_exec.node_name,
                    status=node_exec.status,
                    error=node_exec.error_message,
                    input=node_exec.input_data,
                    process=None,
                    output=output_data,
                    cycle_items=cycle_items,
                    elapsed_time=node_exec.elapsed_time,
                    token_usage=node_exec.token_usage,
                )
                node_executions.append(node_execution)
                execution_nodes.append(node_execution)

            if not execution_nodes:
                continue

            # 失败的执行没有 assistant message，直接用 execution id 作为 key
            if execution.status == "failed":
                node_executions_map[f"execution_{str(execution.id)}"] = execution_nodes
                continue

            # completed：通过时序匹配关联到对应的 assistant message
            # 逻辑：找 execution.started_at 之后最近的、未使用的 assistant message
            best_msg = None
            best_dt = None
            for msg in assistant_messages:
                msg_id_str = str(msg.id)
                if msg_id_str in used_message_ids:
                    continue
                if msg.created_at and msg.created_at >= execution.started_at:
                    delta = (msg.created_at - execution.started_at).total_seconds()
                    if best_dt is None or delta < best_dt:
                        best_dt = delta
                        best_msg = msg

            if not best_msg:
                continue

            msg_id_str = str(best_msg.id)
            used_message_ids.add(msg_id_str)
            node_executions_map[msg_id_str] = execution_nodes

        return node_executions, node_executions_map


def _extract_text(data: Optional[dict]) -> str:
    """从 workflow execution 的 input_data / output_data 中提取可读文本。

    优先取 'text'、'content'、'output' 字段；若都没有则 JSON 序列化整个 dict。
    """
    if not data:
        return ""
    for key in ("text", "content", "output", "result", "answer"):
        if key in data and isinstance(data[key], str):
            return data[key]
    import json
    return json.dumps(data, ensure_ascii=False)
