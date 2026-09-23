"""应用日志服务层"""
import uuid
import json
import datetime as dt
from typing import Optional, Tuple, Dict, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import utcnow_naive, to_timestamp_ms, parse_iso_to_utc_naive
from app.core.logging_config import get_business_logger
from app.models.app_model import AppType
from app.models.conversation_model import Conversation, Message
from app.models.workflow_model import WorkflowExecution
from app.repositories.agent_execution_repository import AgentExecutionRepository
from app.repositories.conversation_repository import ConversationRepository, MessageRepository
from app.repositories.workflow_repository import WorkflowExecutionRepository
from app.schemas.app_log_schema import (
    AppLogAgentSummary,
    AppLogConversation,
    AppLogConversationDetail,
    AppLogMessage,
    AppLogNodeExecution,
    LogFileInfo,
)

logger = get_business_logger()


def _to_ms(iso_str: str | None) -> int | None:
    """将 ISO 8601 时间字符串转换为毫秒时间戳，失败返回 None"""
    if not iso_str:
        return None
    try:
        return to_timestamp_ms(parse_iso_to_utc_naive(iso_str))
    except (ValueError, TypeError):
        return None


def _public_resolved_kind(kind: str | None) -> str | None:
    if kind == "pending":
        return "interrupt"
    return kind


class AppLogService:
    """应用日志服务"""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_repository = ConversationRepository(db)
        self.message_repository = MessageRepository(db)
        self.workflow_execution_repository = WorkflowExecutionRepository(db)

    def list_conversations(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        is_draft: Optional[bool] = None,
        keyword: Optional[str] = None,
        start_date: Optional[dt.datetime] = None,
        end_date: Optional[dt.datetime] = None,
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
            start_date: 开始时间（筛选 created_at >= start_date）
            end_date: 结束时间（筛选 created_at <= end_date）

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
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
            }
        )

        # 使用 Repository 查询
        conversations, total = self.conversation_repository.list_app_conversations(
            app_id=app_id,
            workspace_id=workspace_id,
            is_draft=is_draft,
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
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

    def list_workflow_executions(
        self,
        app_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        is_draft: Optional[bool] = None,
        start_date: Optional[dt.datetime] = None,
        end_date: Optional[dt.datetime] = None,
    ) -> tuple[list[WorkflowExecution], int]:
        """分页获取工作流执行记录；以 release_id 区分试运行与已发布调用。"""
        return self.workflow_execution_repository.list_for_app_log(
            app_id=app_id,
            page=page,
            pagesize=pagesize,
            is_draft=is_draft,
            start_date=start_date,
            end_date=end_date,
        )

    def get_workflow_execution_log_detail(
        self,
        app_id: uuid.UUID,
        execution_id: str,
    ) -> AppLogConversationDetail | None:
        """将一次无会话工作流执行转换为与对话日志一致的详情结构。"""
        execution = self.workflow_execution_repository.get_for_app_log(app_id, execution_id)
        if not execution:
            return None

        # 不创建 Conversation 数据库记录，仅使用 execution.id 构造稳定的虚拟会话/消息 ID，
        # 供现有日志前端和 node_executions_map 复用同一套契约。
        virtual_conversation_id = execution.id
        input_message_id = uuid.uuid5(execution.id, "input")
        output_message_id = uuid.uuid5(execution.id, "output")
        input_content = _format_log_json(execution.input_data)
        output_content = execution.error_message or _extract_execution_output(execution.output_data)
        node_executions = _build_nodes_from_output_data(execution.output_data)

        messages = [
            AppLogMessage(
                id=input_message_id,
                conversation_id=virtual_conversation_id,
                role="user",
                content=input_content,
                status=None,
                meta_data={"execution_id": execution.execution_id},
                created_at=execution.started_at,
            ),
            AppLogMessage(
                id=output_message_id,
                conversation_id=virtual_conversation_id,
                role="assistant",
                content=output_content,
                status=execution.status,
                meta_data={
                    "execution_id": execution.execution_id,
                    "trigger_type": execution.trigger_type,
                    "release_id": str(execution.release_id) if execution.release_id else None,
                    "usage": execution.token_usage,
                    "elapsed_time": execution.elapsed_time,
                    "error_node_id": execution.error_node_id,
                },
                created_at=execution.completed_at or execution.started_at,
            ),
        ]

        return AppLogConversationDetail(
            id=virtual_conversation_id,
            app_id=execution.app_id,
            user_id=str(execution.triggered_by) if execution.triggered_by else None,
            title=execution.execution_id,
            message_count=len(messages),
            is_draft=execution.release_id is None,
            created_at=execution.started_at,
            updated_at=execution.completed_at or execution.started_at,
            messages=messages,
            node_executions_map={str(output_message_id): node_executions} if node_executions else {},
            pending_intervention={},
        )

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

        if app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
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

    def build_pending_intervention_map(
        self,
        conversation_id: uuid.UUID,
    ) -> Dict[str, Dict[str, Any]]:
        """
        聚合某会话下所有 WorkflowExecution 的人工介入信息。

        返回结构与 /public/share/conversations/{conversation_id} 接口的
        pending_intervention 完全一致：
          {
            message_id: {
              "execution_id": ...,
              "status": ...,
              "interventions": [ {node_id, node_name, rendered_content, ...}, ... ]
            }
          }
        """
        intervention_map: Dict[str, Dict[str, Any]] = {}
        executions = list(
            self.db.scalars(
                select(WorkflowExecution)
                .where(WorkflowExecution.conversation_id == conversation_id)
                .order_by(WorkflowExecution.created_at.asc())
            ).all()
        )
        for wf_exec in executions:
            intr_ctx = (wf_exec.context or {}).get("human_intervention", {})
            if not intr_ctx:
                continue
            message_id = intr_ctx.get("message_id")
            if not message_id:
                continue

            resolved_list = intr_ctx.get("resolved_interventions") or []
            pending_list = intr_ctx.get("interventions") or []
            # ★ 修复并行人工介入节点丢失：写入端把所有并行 HITL 节点都放进
            # intervention_backlog（字段最完整：含 node_name/form_fields/timeout_at），
            # 而 interventions 只放"当前可见"子集，且 resume 会把它重写为仅含
            # node_id/rendered_content/actions/interrupt_id 的精简字典（丢 node_name 等）。只读 interventions+resolved 会让
            # 未展示的并行节点整条丢失、可见节点的 node_name/form_fields/timeout_at 为空。
            # 故把 backlog 一并纳入合并，以其完整字段为底，interventions 精简字段仅在 backlog 缺省时补位。
            backlog_list = intr_ctx.get("intervention_backlog") or []

            # Merge by node_id. Order: resolved first, then pending overlays
            # non-resolved fields. Crucially, pending data must NOT clobber
            # already-resolved action_id/form_data with null — the resolved
            # data wins for those fields.
            merged_by_node: Dict[str, Dict[str, Any]] = {
                i["node_id"]: dict(i) for i in resolved_list if i.get("node_id")
            }
            # 先以 backlog（全量并行 HITL，字段最完整）打底，确保每个节点都入场
            for i in backlog_list:
                nid = i.get("node_id")
                if not nid:
                    continue
                base = merged_by_node.get(nid, {})
                merged = dict(base)
                for k, v in i.items():
                    if k in ("resolved_action_id", "resolved_form_data", "resolved_at", "resolved_kind"):
                        if base.get(k) in (None, "", []):
                            merged[k] = v
                    else:
                        merged[k] = v
                merged_by_node[nid] = merged
            # interventions（当前可见子集，字段可能被 resume 精简）叠加：仅在
            # backlog 未提供该字段时补位，避免用精简字典的空值覆盖完整字段
            for i in pending_list:
                nid = i.get("node_id")
                if not nid:
                    continue
                base = merged_by_node.get(nid, {})
                merged = dict(base)
                for k, v in i.items():
                    if k in ("resolved_action_id", "resolved_form_data", "resolved_at", "resolved_kind"):
                        if base.get(k) in (None, "", []):
                            merged[k] = v
                    else:
                        if base.get(k) in (None, "", []):
                            merged[k] = v
                merged_by_node[nid] = merged

            def _sort_key(item: Dict[str, Any]):
                return (
                    item.get("resolved_at") or "9999-12-31T23:59:59",
                    item.get("node_id") or "",
                )

            ordered = sorted(merged_by_node.values(), key=_sort_key)

            # Determine intervention status based on whether ALL intervention nodes
            # have been resolved, NOT based on the workflow's overall execution status.
            # When a HITL node enters the timeout branch, it has completed (resolved)
            # its intervention — the node chose the timeout path. Even if the overall
            # workflow later fails on a downstream node, the intervention itself is done.
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
        return intervention_map

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
                WorkflowExecution.status.in_(["completed", "failed", "waiting_human", "timeout", "cancelled"])
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

        # 查该会话下所有 assistant 消息，用于把 WorkflowExecution 关联到真实的 Message.id
        # 关联方式：
        #   1) 优先按 meta_data.execution_id 精确匹配（waiting_human / 失败更新后的消息会带这个字段）
        #   2) 否则按 Message.created_at 与 execution.completed_at 的时间接近度匹配
        #   3) 兜底使用 uuid.uuid5(execution.id, "assistant")（保持向后兼容）
        assistant_msgs_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.asc())
        )
        assistant_msgs = list(self.db.scalars(assistant_msgs_stmt).all())

        exec_id_to_msg_id: dict[str, uuid.UUID] = {}
        msgs_without_exec_id: list[Message] = []
        for m in assistant_msgs:
            mid = (m.meta_data or {}).get("execution_id") if isinstance(m.meta_data, dict) else None
            if mid:
                exec_id_to_msg_id.setdefault(str(mid), m.id)
            else:
                msgs_without_exec_id.append(m)

        # 按时间正序排列，便于每个 execution 按顺序就近消费
        msgs_without_exec_id.sort(key=lambda m: m.created_at)

        def _resolve_assistant_msg_id(execution: WorkflowExecution) -> uuid.UUID:
            """解析 execution 对应的真实 assistant Message.id，失败则返回合成 UUID。"""
            if execution.execution_id in exec_id_to_msg_id:
                return exec_id_to_msg_id[execution.execution_id]
            target = execution.completed_at or execution.started_at
            if target and msgs_without_exec_id:
                best_idx = 0
                best_diff = abs((msgs_without_exec_id[0].created_at - target).total_seconds())
                for idx, m in enumerate(msgs_without_exec_id[1:], start=1):
                    diff = abs((m.created_at - target).total_seconds())
                    if diff < best_diff:
                        best_diff = diff
                        best_idx = idx
                # 仅在 5 分钟以内认为匹配，避免错配到无关消息
                if best_diff <= 300:
                    matched = msgs_without_exec_id.pop(best_idx)
                    return matched.id
            return uuid.uuid5(execution.id, "assistant")

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
            started_at = execution.started_at or dt.utcnow_naive()
            completed_at = execution.completed_at or started_at

            # assistant message 的 id，同时作为 node_executions_map 的 key
            # 优先解析到真实的 Message.id，使前端可以通过 pending_intervention 的 key
            # 在 messages[] 里定位到对应消息
            assistant_msg_id = _resolve_assistant_msg_id(execution)

            # --- user message（输入）---
            input_data = execution.input_data or {}
            input_content = input_data.get("message") or _extract_text(input_data)

            # 跳过没有用户输入的 execution（如开场白触发的记录）
            if not input_content:
                continue

            files = input_data.get("files") or []
            file_infos = []
            for f in files:
                if isinstance(f, dict) and f.get("url"):
                    file_infos.append(LogFileInfo(
                        type=f.get("type", ""),
                        url=f["url"],
                        name=f.get("name"),
                        size=f.get("size"),
                        file_type=f.get("file_type"),
                    ))
            user_msg = AppLogMessage(
                id=uuid.uuid5(execution.id, "user"),
                conversation_id=conversation_id,
                role="user",
                content=input_content,
                meta_data={"files": files} if files else None,
                files=file_infos,
                created_at=started_at,
            )
            messages.append(user_msg)

            # --- assistant message（输出）---
            if execution.status == "completed":
                # 输出审查触发时，用预设内容替代原始 AI 回复
                if isinstance(execution.output_data, dict) and execution.output_data.get("moderation_flagged"):
                    output_content = execution.output_data.get("preset_response", "") or _extract_text(execution.output_data)
                else:
                    output_content = _extract_text(execution.output_data)
                meta = {"usage": execution.token_usage or {}, "elapsed_time": execution.elapsed_time}
                logical_outputs = _extract_workflow_outputs(execution.output_data)
                if logical_outputs:
                    meta["outputs"] = logical_outputs
            elif execution.status == "waiting_human":
                # waiting_human 状态下工作流暂停等待人工介入，没有 AI 输出文本。
                # 不应将 output_data 原始 JSON dump 为 content（包含 node_outputs 等内部数据）。
                output_content = ""
                intervention_ctx = (execution.context or {}).get("human_intervention", {})
                meta = {
                    "waiting_human": True,
                    "intervention": intervention_ctx,
                    "elapsed_time": execution.elapsed_time,
                }
            elif execution.status == "timeout":
                output_content = execution.error_message or ""
                meta = {"timeout": True, "error_node_id": execution.error_node_id, "elapsed_time": execution.elapsed_time}
            elif execution.status == "cancelled":
                output_content = _extract_text(execution.output_data) if execution.output_data else ""
                meta = {"cancelled": True, "elapsed_time": execution.elapsed_time}
                logical_outputs = _extract_workflow_outputs(execution.output_data) if execution.output_data else None
                if logical_outputs:
                    meta["outputs"] = logical_outputs
            else:
                # failed 状态：优先用 error_message，不要 dump output_data
                output_content = execution.error_message or ""
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
            WorkflowExecution.status.in_(["completed", "failed", "waiting_human", "timeout", "cancelled"])
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

            if execution.status == "waiting_human":
                intervention_ctx = (execution.context or {}).get("human_intervention", {})
                msg_id = intervention_ctx.get("message_id")
                key = msg_id if msg_id else f"execution_{str(execution.id)}"
                node_executions_map[key] = execution_nodes
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

        多 Agent 集群的形态（方案 A）：子 Agent 折叠成该消息下一个
        node_type='agent' + agent_log=<trace> 的 node，与工作流智能体节点同构，
        详情页因此零改动即可渲染成同款样式。

        Args:
            conversation_id: 会话 ID
            messages: 消息列表

        Returns:
            按 message_id 分组的节点执行记录字典
        """
        agent_exec_repo = AgentExecutionRepository(self.db)
        # 锚点只取主 Agent 记录（agent_role='master'）。子 Agent 记录必须挂到主记录
        # 对应的 message 下；不过滤的话子记录会被当作独立执行、吸附到别的消息上，
        # 主日志列表也会被污染。（单 Agent 应用的记录 server_default 即 master，行为不变。）
        executions = agent_exec_repo.get_by_conversation(conversation_id, agent_role="master")

        # 一次取回本会话全部子 Agent 记录并按 parent 分组，避免逐 master 查 N+1
        subs_by_parent: dict[str, list] = {}
        orphan_subs: list = []
        for sub in agent_exec_repo.list_sub_by_conversation(conversation_id):
            if sub.parent_execution_id is not None:
                subs_by_parent.setdefault(str(sub.parent_execution_id), []).append(sub)
            else:
                orphan_subs.append(sub)

        if not executions and not orphan_subs:
            return {}

        node_executions_map: dict[str, list[AppLogNodeExecution]] = {}

        # 筛选 assistant 消息用于时序匹配
        assistant_messages = [m for m in messages if m.role == "assistant"]
        used_message_ids: set[str] = set()

        def _match_message(started_at, ignore_used: bool = False) -> Optional[str]:
            """把一次执行的开始时间吸附到最近一条 assistant 消息。

            args:
                ignore_used: True 时不受"已被占用"限制。同一轮里的多个子 Agent
                    并发跑在同一条 assistant 消息下，必须允许共享该消息，否则
                    第二个子 Agent 会因为没有空闲消息而被整条丢掉（2026-09-14）。

            returns message_id 字符串；无可用候选时返回 None。
            """
            if not started_at:
                return None
            best_msg = None
            best_dt = None
            for msg in assistant_messages:
                msg_id_str = str(msg.id)
                if not ignore_used and msg_id_str in used_message_ids:
                    continue
                if msg.created_at and msg.created_at >= started_at:
                    delta = (msg.created_at - started_at).total_seconds()
                    if best_dt is None or delta < best_dt:
                        best_dt = delta
                        best_msg = msg
            if best_msg is None:
                return None
            used_message_ids.add(str(best_msg.id))
            return str(best_msg.id)

        # 第一轮：解析主记录落到哪条消息。优先 message_id，缺失时按时序吸附；
        # 结果缓存在 key_by_execution，供孤儿子记录复用（避免二次吸附到别的消息）。
        key_by_execution: dict[str, str] = {}
        for execution in executions:
            steps = execution.steps or []
            child_nodes: list[AppLogNodeExecution] = self._build_child_nodes(
                subs_by_parent.get(str(execution.id), [])
            )
            execution_nodes = _steps_to_node_executions(steps) if steps else []

            if not execution_nodes and not child_nodes:
                continue

            key = str(execution.message_id) if execution.message_id else _match_message(execution.started_at)
            if not key:
                continue

            key_by_execution[str(execution.id)] = key
            # 子 Agent 节点追加到同一个 message 的 node 列表末尾
            node_executions_map.setdefault(key, []).extend(execution_nodes + child_nodes)

        # 父记录创建失败时（观测链路降级）子记录会带 parent_execution_id=NULL：
        # 先按"同会话 + started_at 就近"归到最近的 master 上；**连 master 都没有**
        # （例如主记录建行失败）时直接按时序吸附到 assistant 消息 —— 否则这一整轮的
        # 子 Agent 调用链会在详情页彻底不可见（2026-09-14 就是这种情况）。
        for sub in orphan_subs:
            target_key = None
            target_id = None
            if executions:
                target = None
                best_delta = None
                for execution in executions:
                    if not execution.started_at or not sub.started_at:
                        continue
                    if sub.started_at < execution.started_at:
                        continue
                    delta = (sub.started_at - execution.started_at).total_seconds()
                    if best_delta is None or delta < best_delta:
                        best_delta = delta
                        target = execution
                if target is not None:
                    # 命中已铺出 node 的 master：复用其消息 key；master 自己没铺出 node
                    # 才退化为独立时序吸附（此时 key_by_execution 里没有它）。
                    target_key = key_by_execution.get(str(target.id))
                    target_id = str(target.id)
            if target_key is None:
                # 同一轮可有多条孤儿子记录（并发子 Agent），允许共享同一条消息
                target_key = _match_message(sub.started_at, ignore_used=True)
            if target_key is None:
                continue
            # 只修正投影出来的 node（不回写 ORM 对象，避免只读查询意外触发 flush）
            node = self._sub_execution_to_node(sub)
            if target_id:
                node.parent_execution_id = target_id
                node.meta = {**(node.meta or {}), "parent_execution_id": target_id}
            node_executions_map.setdefault(target_key, []).append(node)

        return node_executions_map

    def _build_child_nodes(self, subs: list) -> list[AppLogNodeExecution]:
        """把一组子 Agent 执行记录投影为节点列表（每个子记录 = 一个 agent 节点）。

        没有 trace 的子记录（例如协作模式的节点激活）额外铺开其"非 agent 类"步骤
        （工具调用 / handoff），至少能看到调用链时序；agent 类步骤的输入产出已挂在
        agent 节点自身，再铺开会得到一个重复的同名节点。
        """
        nodes: list[AppLogNodeExecution] = []
        for sub in subs or []:
            nodes.append(self._sub_execution_to_node(sub))
            if not _execution_has_trace(sub):
                nodes.extend(_steps_to_node_executions([
                    s for s in (sub.steps or [])
                    if isinstance(s, dict) and s.get("node_type") != "agent"
                ]))
        return nodes

    @staticmethod
    def _sub_execution_to_node(sub, depth: int = 1) -> AppLogNodeExecution:
        """把一条子 Agent 执行记录折叠成一个 node_type='agent' 的节点。

        与工作流智能体节点同构：agent_log 承载 trace（ROUND / llm / tool_calls），
        input/output/elapsed_time/token_usage 照常填充 —— 前端 Runtime.tsx 已有
        `node_type === 'agent'` 分支，详情页因此无需任何改动。

        缺 trace 时降级为 {"meta": ..., "iterations": []}：节点仍可点开看到
        input/output 与状态，不会因为观测数据缺失而整块消失。
        """
        meta = sub.meta_data if isinstance(sub.meta_data, dict) else {}
        steps = sub.steps or []
        agent_name = meta.get("agent_name") or "子 Agent"

        trace = sub.agent_log
        # 兼容旧数据/兜底路径：trace 曾落在 meta_data.agent_log
        if not (isinstance(trace, dict) and trace.get("iterations")):
            legacy = meta.get("agent_log")
            if isinstance(legacy, dict) and legacy.get("iterations"):
                trace = legacy
        if not isinstance(trace, dict):
            trace = {
                "meta": {"model": meta.get("model"), "agent_name": agent_name},
                "iterations": [],
            }

        first_step = steps[0] if steps and isinstance(steps[0], dict) else None
        last_step = steps[-1] if steps and isinstance(steps[-1], dict) else None
        # 协作模式的 steps 里还夹着 handoff 步骤：节点自身的输入/产出应取"agent 类"步骤
        agent_steps = [s for s in steps if isinstance(s, dict) and s.get("node_type") == "agent"]
        if agent_steps:
            first_step = agent_steps[0]
            last_step = agent_steps[-1]

        # 输入：优先用派发时记录的任务（子 Agent 收到的子问题），而不是工具的入参。
        # 主管模式的 steps 只有工具步骤，取 first_step 会显示成 `{"query": "..."}`。
        input_payload = meta.get("task") or (first_step.get("input") if first_step else None)
        # 输出：trace 里最后一轮 LLM 回答才是"交付物"；退化时才用最后一次工具结果。
        output_payload = _trace_final_output(trace)
        if output_payload is None:
            output_payload = last_step.get("output") if last_step else None

        return AppLogNodeExecution(
            node_id=str(sub.id),
            node_type="agent",
            node_name=agent_name,
            status=sub.status or "completed",
            error=sub.error_message,
            input=input_payload,
            output=output_payload,
            agent_log=trace,
            elapsed_time=sub.elapsed_time,
            token_usage=sub.token_usage,
            meta={
                "agent_id": meta.get("agent_id"),
                "agent_name": agent_name,
                "execution_id": str(sub.id),
                "parent_execution_id": str(sub.parent_execution_id) if sub.parent_execution_id else None,
                "depth": depth,
                "orchestration_mode": sub.orchestration_mode,
            },
            agent_id=meta.get("agent_id"),
            agent_name=agent_name,
            execution_id=str(sub.id),
            parent_execution_id=str(sub.parent_execution_id) if sub.parent_execution_id else None,
            depth=depth,
            orchestration_mode=sub.orchestration_mode,
        )

    def build_agent_execution_summary(
        self,
        node_executions_map: dict[str, list[AppLogNodeExecution]],
    ) -> dict[str, list[AppLogAgentSummary]]:
        """按 assistant message_id 汇总本轮集群调用的 Agent 浅层概览。

        B 的入口：只提供数据（几条、各自状态/耗时/token、工具调用次数、迭代轮数），
        本轮前端不渲染；未来做集群概览卡 / 泳道视图时直接消费，不需要动数据模型。
        """
        summary: dict[str, list[AppLogAgentSummary]] = {}
        for msg_id, nodes in (node_executions_map or {}).items():
            agents: list[AppLogAgentSummary] = []
            for node in nodes:
                if getattr(node, "node_type", None) != "agent":
                    continue
                trace = node.agent_log if isinstance(node.agent_log, dict) else {}
                iterations = trace.get("iterations") or []
                tool_count = 0
                for it in iterations:
                    if isinstance(it, dict):
                        tool_count += len(it.get("tool_calls") or [])
                agents.append(AppLogAgentSummary(
                    execution_id=node.execution_id,
                    agent_id=node.agent_id,
                    agent_name=node.agent_name or node.node_name,
                    role="sub",
                    status=node.status,
                    elapsed_time=node.elapsed_time,
                    token_usage=node.token_usage,
                    tool_count=tool_count,
                    iterations=len(iterations),
                ))
            if agents:
                summary[msg_id] = agents
        return summary

    def count_sub_agents_by_conversations(
        self,
        conversation_ids: list[uuid.UUID],
    ) -> dict[str, int]:
        """批量统计会话的子 Agent 执行条数；失败降级为空（不阻断日志列表）。"""
        try:
            return AgentExecutionRepository(self.db).count_sub_by_conversations(conversation_ids)
        except Exception as e:
            logger.warning(f"统计子 Agent 执行条数失败（已降级为 0）: {e}")
            return {}

    def get_message_node_executions(self, message_id: uuid.UUID) -> list[dict]:
        """返回单条 assistant 消息的节点执行明细（agent 工具调用轨迹）。

        供版本切换等接口回填 subContent：优先按 agent_executions.message_id 精确取；
        缺失时按完成时间就近兜底（300s 内视为同一条，同 workflow _build_branch_view 策略）。
        无关联执行记录返回空列表。

        多 Agent 集群：只按 master 记录匹配（否则子记录会被时序吸附到主消息上），
        匹配到 master 后把它的子 Agent 节点一并展开。

        Note: 重新生成的版本以 skip_save=True 跑 LLM，未落库 agent_execution，无轨迹可返回。
        """
        message = self.db.get(Message, message_id)
        if not message or message.role != "assistant":
            return []
        # 开场白等非执行产生的 assistant 消息无真实执行记录：其 meta_data 不含任何执行痕迹
        # （execution_id / regenerated_from / usage），跳过兜底匹配避免被时间就近误吸附到邻近执行
        meta = message.meta_data if isinstance(message.meta_data, dict) else None
        if meta and not any(k in meta for k in ("execution_id", "regenerated_from", "usage")):
            return []

        agent_exec_repo = AgentExecutionRepository(self.db)
        execution = agent_exec_repo.get_by_message_id(message.id, agent_role="master")

        # 兜底：原始首轮回复的 agent_execution.message_id 为空，按完成时间就近匹配
        if not execution and message.created_at:
            target = message.created_at
            best = None
            best_diff = None
            for ex in agent_exec_repo.get_by_conversation(message.conversation_id, agent_role="master"):
                ref = ex.completed_at or ex.started_at
                if not ref:
                    continue
                diff = abs((ref - target).total_seconds())
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = ex
            if best is not None and best_diff is not None and best_diff <= 300:
                execution = best

        if not execution:
            return []

        nodes = _steps_to_node_executions(execution.steps) if execution.steps else []
        child_nodes = self._build_child_nodes(agent_exec_repo.list_by_parent(execution.id))
        if not nodes and not child_nodes:
            return []
        return [n.model_dump() for n in (nodes + child_nodes)]


def _format_log_json(data: Any) -> str:
    """将任意工作流输入/输出以可读 JSON 呈现在日志消息中。"""
    try:
        return json.dumps(data if data is not None else {}, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(data or "")


def _extract_execution_output(data: Any) -> str:
    """提取纯工作流最终输出；复杂结构保留为格式化 JSON。"""
    if not isinstance(data, dict):
        return _format_log_json(data)

    value = data.get("output")
    if isinstance(value, dict):
        for key in ("result", "text", "content", "output", "answer"):
            if value.get(key) is not None:
                candidate = value[key]
                return candidate if isinstance(candidate, str) else _format_log_json(candidate)
    elif value is not None:
        return value if isinstance(value, str) else _format_log_json(value)

    text = _extract_text(data)
    return text or _format_log_json(data)


def _extract_text(data: Optional[dict]) -> str:
    """从 workflow execution 的 input_data / output_data 中提取可读文本。

    优先取 'text'、'content'、'output' 字段（字符串或可转为字符串的值）；
    若都没有则返回空字符串（不再将整个 dict dump 为 JSON，避免暴露 node_outputs
    等内部数据作为 assistant 消息的 content）。
    """
    if not data:
        return ""
    for key in ("message", "text", "content", "output", "result", "answer"):
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            return val
        # output_data["output"] 可能是 End 节点的输出字符串，直接转 str
        if key == "output" and val is not None:
            return str(val)
    return ""


def _extract_workflow_outputs(data: Optional[dict]) -> list[dict]:
    """Return logical Answer/End outputs without flattening them together.

    New executions store ``output_data.outputs`` directly.  The fallback reads
    completed End/Output node results so older executions can still expose
    separate replies in the log API.
    """
    if not isinstance(data, dict):
        return []

    outputs = data.get("outputs")
    if isinstance(outputs, list):
        return [item for item in outputs if isinstance(item, dict) and item.get("node_id")]

    node_outputs = data.get("node_outputs") or {}
    result: list[dict] = []
    for node_id, node_data in node_outputs.items():
        if not isinstance(node_data, dict):
            continue
        node_type = node_data.get("node_type")
        if node_type not in ("end", "output", "answer"):
            continue
        content = node_data.get("output", "")
        if isinstance(content, dict):
            content = content.get("output") or content.get("text") or content.get("content") or ""
        result.append({
            "node_id": node_id,
            "content": str(content) if content is not None else "",
            "status": node_data.get("status", "completed"),
        })
    return result


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
        agent_log = output.pop("agent_log", None)
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
            agent_log=agent_log,
            output=output if output else None,
            cycle_items=cycle_items,
            elapsed_time=elapsed_time,
            token_usage=token_usage,
        ))
    return result


def _execution_has_trace(execution) -> bool:
    """判断执行记录是否带有可渲染的 trace（新列 agent_log，兼容旧数据 meta_data.agent_log）"""
    trace = getattr(execution, "agent_log", None)
    if isinstance(trace, dict) and trace.get("iterations"):
        return True
    meta = getattr(execution, "meta_data", None)
    if isinstance(meta, dict):
        legacy = meta.get("agent_log")
        if isinstance(legacy, dict) and legacy.get("iterations"):
            return True
    return False


def _trace_final_output(trace: Optional[dict]) -> Optional[str]:
    """从 trace 里取子 Agent 的"最终产出"。

    `AgentTraceRecorder` 的 trace 是 `{meta, iterations:[{llm:{...}, tool_calls:[...]}]}`。
    子 Agent 的 steps 里只有工具步骤（web_search 等），**没有** agent 步骤，因此
    input/output 不能从 steps 取 —— 取到的会是"某次工具调用的入参/结果"，显示成
    "输出 = 搜索结果"；真正该展示的是它最后一轮 LLM 的回答。倒序找第一条非空
    `llm.output`（最后一轮通常没有 tool_calls，其 output 即为终稿）。
    """
    if not isinstance(trace, dict):
        return None
    for iteration in reversed(trace.get("iterations") or []):
        if not isinstance(iteration, dict):
            continue
        llm = iteration.get("llm") or {}
        output = llm.get("output")
        if isinstance(output, str) and output.strip():
            return output
    return None


def _steps_to_node_executions(steps: Optional[list]) -> list[AppLogNodeExecution]:
    """将 agent_executions.steps（JSONB 数组）转为 AppLogNodeExecution 列表。

    step 结构：{step_id, node_type, node_name, status, input, output, elapsed_time, error, meta}。
    输出与 _build_nodes_from_output_data（workflow 侧）同构，前端可走同一套 subContent 渲染。
    """
    nodes: list[AppLogNodeExecution] = []
    for idx, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        nodes.append(AppLogNodeExecution(
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
    return nodes
