"""应用日志服务层"""
import uuid
from typing import Optional, Tuple
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.models.conversation_model import Conversation, Message
from app.models.workflow_model import WorkflowExecution
from app.repositories.conversation_repository import ConversationRepository, MessageRepository
from app.schemas.app_log_schema import AppLogNodeExecution

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
        workspace_id: uuid.UUID
    ) -> Tuple[Conversation, dict[str, list[AppLogNodeExecution]]]:
        """
        查询会话详情（包含消息和工作流节点执行记录）

        Args:
            app_id: 应用 ID
            conversation_id: 会话 ID
            workspace_id: 工作空间 ID

        Returns:
            Tuple[Conversation, dict[str, list[AppLogNodeExecution]]]:
                (包含消息的会话对象, 按消息ID分组的节点执行记录)

        Raises:
            ResourceNotFoundException: 当会话不存在时
        """
        logger.info(
            "查询应用日志会话详情",
            extra={
                "app_id": str(app_id),
                "conversation_id": str(conversation_id),
                "workspace_id": str(workspace_id)
            }
        )

        # 查询会话
        conversation = self.conversation_repository.get_conversation_for_app_log(
            conversation_id=conversation_id,
            app_id=app_id,
            workspace_id=workspace_id
        )

        # 查询消息（按时间正序）
        messages = self.message_repository.get_messages_by_conversation(
            conversation_id=conversation_id
        )

        # 将消息附加到会话对象
        conversation.messages = messages

        # 查询工作流节点执行记录（按消息分组）
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

        return conversation, node_executions_map

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
            WorkflowExecution.status == "completed"
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
            if not execution.output_data:
                continue

            # 找到该 execution 对应的 assistant message
            # 逻辑：找 execution.started_at 之后最近的、未使用的 assistant message
            best_msg = None
            best_dt = None
            for msg in assistant_messages:
                msg_id_str = str(msg.id)
                if msg_id_str in used_message_ids:
                    continue
                if msg.created_at and msg.created_at >= execution.started_at:
                    dt = (msg.created_at - execution.started_at).total_seconds()
                    if best_dt is None or dt < best_dt:
                        best_dt = dt
                        best_msg = msg

            if not best_msg:
                continue

            msg_id_str = str(best_msg.id)
            used_message_ids.add(msg_id_str)

            # 提取节点输出
            output_data = execution.output_data
            if isinstance(output_data, dict):
                node_outputs = output_data.get("node_outputs", {})
                execution_nodes = []
                for node_id, node_data in node_outputs.items():
                    if not isinstance(node_data, dict):
                        continue
                    node_execution = AppLogNodeExecution(
                        node_id=node_data.get("node_id", node_id),
                        node_type=node_data.get("node_type", "unknown"),
                        node_name=node_data.get("node_name"),
                        status=node_data.get("status", "unknown"),
                        error=node_data.get("error"),
                        input=node_data.get("input"),
                        process=node_data.get("process"),
                        output=node_data.get("output"),
                        elapsed_time=node_data.get("elapsed_time"),
                        token_usage=node_data.get("token_usage"),
                    )
                    node_executions.append(node_execution)
                    execution_nodes.append(node_execution)

                # 将节点记录关联到 message_id
                node_executions_map[msg_id_str] = execution_nodes

        return node_executions, node_executions_map
