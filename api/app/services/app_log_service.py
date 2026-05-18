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
from app.repositories.agent_execution_repository import AgentExecutionRepository
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
            keyword: 搜索关键词（匹配 messages 表消息内容）

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
                "keyword": keyword,
            }
        )

        # 使用 Repository 查询
        conversations, total = self.conversation_repository.list_app_conversations(
            app_id=app_id,
            workspace_id=workspace_id,
            is_draft=is_draft,
            keyword=keyword,
            page=page,
            pagesize=pagesize,
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
            node_executions_map = self._get_agent_node_executions(conversation_id, messages)

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
          - user message：来自 execution.input_data（content 取 message 字段，files 放 meta_data）
          - assistant message：来自 execution.output_data（失败时内容为错误信息）
        开场白的 suggested_questions 合并到第一条 assistant message 的 meta_data 里。

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

        # 查开场白：Message 表里 meta_data 含 suggested_questions 的第一条 assistant 消息
        opening_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.asc())
            .limit(10)
        )
        early_messages = list(self.db.scalars(opening_stmt).all())
        suggested_questions: list = []
        for m in early_messages:
            if isinstance(m.meta_data, dict) and "suggested_questions" in m.meta_data:
                suggested_questions = m.meta_data.get("suggested_questions") or []
                break

        messages: list[AppLogMessage] = []
        node_executions_map: dict[str, list[AppLogNodeExecution]] = {}

        # 如果有开场白，作为第一条 assistant 消息插入
        if suggested_questions or early_messages:
            opening_msg = next(
                (m for m in early_messages
                 if isinstance(m.meta_data, dict) and "suggested_questions" in m.meta_data),
                None
            )
            if opening_msg:
                messages.append(AppLogMessage(
                    id=opening_msg.id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=opening_msg.content,
                    status=None,
                    meta_data={"suggested_questions": suggested_questions},
                    created_at=opening_msg.created_at,
                ))

        for execution in executions:
            started_at = execution.started_at or dt.datetime.now()
            completed_at = execution.completed_at or started_at

            # assistant message 的 id，同时作为 node_executions_map 的 key
            assistant_msg_id = uuid.uuid5(execution.id, "assistant")

            # --- user message（输入）---
            input_data = execution.input_data or {}
            input_content = input_data.get("message") or _extract_text(input_data)

            # 跳过没有用户输入的 execution（如开场白触发的记录）
            if not input_content or not input_content.strip():
                continue

            files = input_data.get("files") or []
            user_msg = AppLogMessage(
                id=uuid.uuid5(execution.id, "user"),
                conversation_id=conversation_id,
                role="user",
                content=input_content,
                meta_data={"files": files} if files else None,
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

            # --- 节点执行记录，从 workflow_executions.output_data["node_outputs"] 读取 ---
            execution_nodes = _build_nodes_from_output_data(execution.output_data)

            if execution_nodes:
                node_executions_map[str(assistant_msg_id)] = execution_nodes

        return messages, node_executions_map

    def _get_workflow_node_executions_with_map(
        self,
        conversation_id: uuid.UUID,
        messages: list[Message]
    ) -> dict[str, list[AppLogNodeExecution]]:
        """
        从 workflow_executions 表中提取节点执行记录，并按 assistant message 分组

        Args:
            conversation_id: 会话 ID
            messages: 消息列表

        Returns:
            Tuple[list[AppLogNodeExecution], dict[str, list[AppLogNodeExecution]]]:
                (所有节点执行记录列表, 按 message_id 分组的节点执行记录字典)
        """
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
            # 构建节点执行记录列表，从 workflow_executions.output_data["node_outputs"] 读取
            execution_nodes = _build_nodes_from_output_data(execution.output_data)

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
            
        return node_executions_map

    def _get_agent_node_executions(
        self,
        conversation_id: uuid.UUID,
        messages: list[Message]
    ) -> dict[str, list[AppLogNodeExecution]]:
        """从 agent_executions 表中读取 Agent 应用的节点执行记录

        Args:
            conversation_id: 会话 ID
            messages: 消息列表

        Returns:
            按 message_id 分组的节点执行记录字典
        """
        agent_exec_repo = AgentExecutionRepository(self.db)
        executions = agent_exec_repo.get_by_conversation(conversation_id)

        if not executions:
            return {}

        node_executions_map: dict[str, list[AppLogNodeExecution]] = {}

        # 筛选 assistant 消息用于时序匹配
        assistant_messages = [m for m in messages if m.role == "assistant"]
        used_message_ids: set[str] = set()

        for execution in executions:
            steps = execution.steps or []
            if not steps:
                continue

            # 将 steps 转换为 AppLogNodeExecution 列表
            execution_nodes = []
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                execution_nodes.append(AppLogNodeExecution(
                    node_id=step.get("step_id", f"agent_step_{idx}"),
                    node_type=step.get("node_type", "tool"),
                    node_name=step.get("node_name"),
                    status=step.get("status", "completed"),
                    error=step.get("error"),
                    input=step.get("input"),
                    output=step.get("output"),
                    elapsed_time=step.get("elapsed_time"),
                    token_usage=None,
                    meta=step.get("meta"),
                ))

            if not execution_nodes:
                continue

            # 优先使用 message_id 关联
            if execution.message_id:
                node_executions_map[str(execution.message_id)] = execution_nodes
                continue

            # 回退：通过时序匹配关联到对应的 assistant message
            best_msg = None
            best_dt = None
            for msg in assistant_messages:
                msg_id_str = str(msg.id)
                if msg_id_str in used_message_ids:
                    continue
                if msg.created_at and execution.started_at and msg.created_at >= execution.started_at:
                    delta = (msg.created_at - execution.started_at).total_seconds()
                    if best_dt is None or delta < best_dt:
                        best_dt = delta
                        best_msg = msg

            if best_msg:
                msg_id_str = str(best_msg.id)
                used_message_ids.add(msg_id_str)
                node_executions_map[msg_id_str] = execution_nodes

        return node_executions_map


def _extract_text(data: Optional[dict]) -> str:
    """从 workflow execution 的 input_data / output_data 中提取可读文本。

    优先取 'text'、'content'、'output' 字段；若都没有则 JSON 序列化整个 dict。
    """
    if not data:
        return ""
    for key in ("message", "text", "content", "output", "result", "answer"):
        if key in data and isinstance(data[key], str):
            return data[key]
    import json
    return json.dumps(data, ensure_ascii=False)


def _build_nodes_from_output_data(output_data: Optional[dict]) -> list[AppLogNodeExecution]:
    """从 workflow_executions.output_data["node_outputs"] 构建节点执行记录列表。

    output_data 结构：
    {
        "node_outputs": {
            "<node_id>": {
                "node_type": ...,
                "node_name": ...,
                "status": ...,
                "input": ...,
                "output": ...,
                "elapsed_time": ...,
                "token_usage": ...,
                "error": ...,
                "cycle_items": [...],
                ...
            }
        },
        "error": ...,
        ...
    }
    """
    if not output_data:
        return []
    node_outputs: dict = output_data.get("node_outputs") or {}
    # 按 execution_order（节点执行时写入的单调递增序号）排序。
    # PostgreSQL JSONB 不保证 key 顺序，不能依赖 dict 插入顺序；
    # 缺失 execution_order 的历史数据退化到 0，保持在最前。
    ordered_items = sorted(
        node_outputs.items(),
        key=lambda kv: (kv[1] or {}).get("execution_order", 0)
        if isinstance(kv[1], dict) else 0
    )
    result = []
    for node_id, node_data in ordered_items:
        if not isinstance(node_data, dict):
            continue
        output = dict(node_data)
        cycle_items = output.pop("cycle_items", None)
        # 把已知的顶层字段剥离，剩余的作为 output
        node_type = output.pop("node_type", "unknown")
        node_name = output.pop("node_name", None)
        status = output.pop("status", "completed")
        error = output.pop("error", None)
        inp = output.pop("input", None)
        elapsed_time = output.pop("elapsed_time", None)
        token_usage = output.pop("token_usage", None)
        process = output.pop("process", None)
        # execution_order 仅用于排序，不返回给前端
        output.pop("execution_order", None)
        result.append(AppLogNodeExecution(
            node_id=node_id,
            node_type=node_type,
            node_name=node_name,
            status=status,
            error=error,
            input=inp,
            process=process,
            output=output if output else None,
            cycle_items=cycle_items,
            elapsed_time=elapsed_time,
            token_usage=token_usage,
        ))
    return result
