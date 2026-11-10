"""Application-log service (frozen port of the monolith ``AppLogService``).

Covers the conversation list, the conversation-less workflow execution list /
detail, the conversation message detail with per-message node executions, the
pending human-intervention aggregation and the multi-agent cluster summary.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models.app_model import AppType
from ..models.conversation_model import Conversation, Message
from ..models.file_metadata_model import FileMetadata
from ..models.workflow_model import WorkflowExecution
from ..repositories.agent_execution_repository import AgentExecutionRepository
from ..repositories.conversation_repository import ConversationRepository, MessageRepository
from ..repositories.workflow_repository import WorkflowExecutionRepository
from ..schemas.app_log_schema import (
    AppLogAgentSummary,
    AppLogConversationDetail,
    AppLogMessage,
    AppLogNodeExecution,
    LogFileInfo,
)
from ..utils.datetime_utils import parse_iso_to_utc_naive, to_timestamp_ms, utcnow_naive
from ..utils.file_urls import build_permanent_file_url

logger = logging.getLogger(__name__)


def _to_ms(iso_str: str | None) -> int | None:
    """ISO 8601 string -> millisecond timestamp; None on parse failure."""

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
    """Application-log queries."""

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
        is_draft: bool | None = None,
        keyword: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[Conversation], int]:
        """Paginated app-log conversation list; returns (rows, total)."""

        logger.info(
            "Querying app-log conversation list",
            extra={
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "page": page,
                "pagesize": pagesize,
                "is_draft": is_draft,
                "keyword": keyword,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
            },
        )

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
            "App-log conversation list retrieved",
            extra={
                "app_id": str(app_id),
                "total": total,
                "returned": len(conversations),
            },
        )

        return conversations, total

    def list_workflow_executions(
        self,
        app_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        is_draft: bool | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[WorkflowExecution], int]:
        """Paginated workflow execution records; ``release_id`` distinguishes
        trial runs from calls of a published version."""

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
        """Project one conversation-less workflow execution into the same
        detail shape as conversation logs."""

        execution = self.workflow_execution_repository.get_for_app_log(app_id, execution_id)
        if not execution:
            return None

        # No Conversation row is created; execution.id mints stable virtual
        # conversation/message ids so the existing log frontend and the
        # node_executions_map keep the same contract.
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
            node_executions_map=(
                {str(output_message_id): node_executions} if node_executions else {}
            ),
            pending_intervention={},
        )

    # Visible statuses when rebuilding a workflow conversation from executions.
    _WF_VISIBLE_STATUSES = (
        "completed",
        "failed",
        "waiting_human",
        "timeout",
        "cancelled",
    )

    def get_conversation_detail(
        self,
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        app_type: str = AppType.AGENT,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[Conversation, list, dict[str, list[AppLogNodeExecution]], dict]:
        """One page of a conversation's detail.

        Workflow apps rebuild messages and node logs from
        ``workflow_executions`` (paged per execution), other app types read the
        ``messages`` table (paged per message) and attach only the Agent
        execution steps linked to the page's messages. Pagination prevents a
        long conversation (thousands of rounds) from loading every row and
        JSONB blob into one request — the source of the OOM kills.

        Returns:
            (conversation, messages, node_executions_map, page_meta)
        """

        logger.info(
            "Querying app-log conversation detail",
            extra={
                "app_id": str(app_id),
                "conversation_id": str(conversation_id),
                "workspace_id": str(workspace_id),
                "app_type": app_type,
                "page": page,
                "pagesize": pagesize,
            },
        )

        conversation = self.conversation_repository.get_conversation_for_app_log(
            conversation_id=conversation_id,
            app_id=app_id,
            workspace_id=workspace_id,
        )

        page_execution_ids: set[str] = set()
        if app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            (
                messages,
                node_executions_map,
                has_earlier,
                total,
                page_execution_ids,
            ) = self._get_workflow_messages_and_nodes(
                conversation_id,
                workspace_id,
                page=page,
                pagesize=pagesize,
            )
        else:
            messages, has_earlier = self.message_repository.get_messages_page(
                conversation_id=conversation_id,
                page=page,
                pagesize=pagesize,
            )
            node_executions_map = self._get_agent_node_executions(conversation_id, messages)
            total = self.message_repository.count_messages(conversation_id)

        page_meta = {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            # Page 1 is the newest page; an older page exists when the query
            # found rows beyond this page.
            "hasnext": bool(has_earlier),
            # Internal only: scopes the intervention query; popped before
            # the response is returned.
            "execution_ids": sorted(page_execution_ids),
        }

        logger.info(
            "App-log conversation detail retrieved",
            extra={
                "app_id": str(app_id),
                "conversation_id": str(conversation_id),
                "message_count": len(messages),
                "message_with_nodes_count": len(node_executions_map),
                "has_earlier": has_earlier,
            },
        )

        return conversation, messages, node_executions_map, page_meta

    def build_pending_intervention_map(
        self,
        conversation_id: uuid.UUID,
        execution_ids: set[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Aggregate the human-intervention info of workflow executions of
        one conversation.

        Pass ``execution_ids`` (the page's business execution ids) to bound the
        query to one page; ``None`` keeps the legacy whole-conversation scan for
        callers that still need it.

        The response shape matches the public share endpoint:
          {
            message_id: {
              "execution_id": ...,
              "status": ...,
              "interventions": [ {node_id, node_name, rendered_content, ...}, ... ]
            }
          }
        """

        intervention_map: dict[str, dict[str, Any]] = {}
        stmt = (
            select(WorkflowExecution)
            .where(WorkflowExecution.conversation_id == conversation_id)
            .order_by(WorkflowExecution.created_at.asc())
        )
        if execution_ids is not None:
            stmt = stmt.where(WorkflowExecution.execution_id.in_(execution_ids))
        executions = list(self.db.scalars(stmt).all())
        for wf_exec in executions:
            intr_ctx = (wf_exec.context or {}).get("human_intervention", {})
            if not intr_ctx:
                continue
            message_id = intr_ctx.get("message_id")
            if not message_id:
                continue

            resolved_list = intr_ctx.get("resolved_interventions") or []
            pending_list = intr_ctx.get("interventions") or []
            # Parallel HITL nodes: the writer puts every parallel HITL node in
            # intervention_backlog (the most complete fields: node_name /
            # form_fields / timeout_at), while interventions only carries the
            # currently visible subset and resume rewrites it to a slim dict
            # (node_id/rendered_content/actions/interrupt_id). Reading only
            # interventions+resolved would lose hidden parallel nodes and blank
            # node_name/form_fields/timeout_at on visible ones; the backlog is
            # merged in as the base, the slim fields only fill gaps.
            backlog_list = intr_ctx.get("intervention_backlog") or []

            # Merge by node_id. Order: resolved first, then pending overlays
            # non-resolved fields. Pending data must NOT clobber already
            # resolved action_id/form_data with null.
            merged_by_node: dict[str, dict[str, Any]] = {
                i["node_id"]: dict(i) for i in resolved_list if i.get("node_id")
            }
            # Backlog first (full parallel HITL set, most complete fields) so
            # every node enters the merge.
            for i in backlog_list:
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
                        if base.get(k) in (None, "", []):
                            merged[k] = v
                    else:
                        merged[k] = v
                merged_by_node[nid] = merged
            # interventions (visible subset, possibly slimmed by resume)
            # overlays only where the backlog left the field empty.
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
                        if base.get(k) in (None, "", []):
                            merged[k] = v
                    else:
                        if base.get(k) in (None, "", []):
                            merged[k] = v
                merged_by_node[nid] = merged

            def _sort_key(item: dict[str, Any]):
                return (
                    item.get("resolved_at") or "9999-12-31T23:59:59",
                    item.get("node_id") or "",
                )

            ordered = sorted(merged_by_node.values(), key=_sort_key)

            # The intervention status depends on whether ALL intervention nodes
            # are resolved, not on the workflow's overall execution status: a
            # HITL node that took the timeout branch completed (resolved) its
            # intervention even if a downstream node later fails the workflow.
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
        workspace_id: uuid.UUID,
        *,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[
        list[AppLogMessage],
        dict[str, list[AppLogNodeExecution]],
        bool,
        int,
        set[str],
    ]:
        """Workflow apps only: build one page of messages and node logs from
        ``workflow_executions``.

        Each WorkflowExecution maps to one conversation round:
          - user message: from execution.input_data (content = "message",
            files land in meta_data)
          - assistant message: from execution.output_data (error text on
            failure)
        The opening statement's suggested_questions are returned only with the
        earliest execution page, preserving chronological order when pages merge.

        Paging runs on executions (one round per row) via offset; only the
        page's executions and the assistant messages matching their
        ``execution_id`` values are loaded, so a long conversation never pulls
        every JSONB row into memory.

        Returns:
            (messages, node_executions_map, has_earlier, total,
             page_execution_ids)
        """

        executions, has_earlier = self.workflow_execution_repository.get_conversation_page(
            conversation_id,
            statuses=list(self._WF_VISIBLE_STATUSES),
            page=page,
            pagesize=pagesize,
        )
        total = self.workflow_execution_repository.count_by_conversation_id(
            conversation_id,
            statuses=list(self._WF_VISIBLE_STATUSES),
        )
        page_execution_ids = {
            str(ex.execution_id) for ex in executions if ex.execution_id
        }

        # The opening statement belongs before the oldest execution. Return it
        # only when no earlier execution page remains; the lookup itself stays
        # bounded to the earliest ten assistant messages.
        opening_msg = None
        suggested_questions: list = []
        if executions and not has_earlier:
            opening_stmt = (
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role == "assistant",
                )
                .order_by(Message.created_at.asc())
                .limit(10)
            )
            for m in self.db.scalars(opening_stmt).all():
                if isinstance(m.meta_data, dict) and "suggested_questions" in m.meta_data:
                    opening_msg = m
                    suggested_questions = m.meta_data.get("suggested_questions") or []
                    break

        # New messages carry meta_data.execution_id, so one targeted query can
        # resolve the page without walking the conversation from newest to oldest.
        message_execution_id = Message.meta_data["execution_id"].as_string()
        exec_id_to_msg_id: dict[str, uuid.UUID] = {}
        if page_execution_ids:
            exact_stmt = (
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role == "assistant",
                    Message.is_deleted.is_not(True),
                    message_execution_id.in_(sorted(page_execution_ids)),
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
            for message in self.db.scalars(exact_stmt).all():
                execution_id = (
                    message.meta_data.get("execution_id")
                    if isinstance(message.meta_data, dict)
                    else None
                )
                if execution_id:
                    exec_id_to_msg_id.setdefault(str(execution_id), message.id)

        # Historical assistant messages may not carry execution_id. For each
        # unmatched page execution, load at most ten candidates within five
        # minutes of its completion/start time. Query count and loaded ORM rows
        # are therefore bounded by the requested execution page size.
        match_tolerance = timedelta(minutes=5)
        legacy_candidates_by_id: dict[uuid.UUID, Message] = {}
        for execution in executions:
            execution_id = str(execution.execution_id) if execution.execution_id else ""
            if execution_id in exec_id_to_msg_id:
                continue
            target = execution.completed_at or execution.started_at
            if target is None:
                continue
            candidate_stmt = (
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role == "assistant",
                    Message.is_deleted.is_not(True),
                    Message.created_at >= target - match_tolerance,
                    Message.created_at <= target + match_tolerance,
                    or_(Message.meta_data.is_(None), message_execution_id.is_(None)),
                )
                .order_by(
                    func.abs(func.extract("epoch", Message.created_at - target)),
                    Message.id.asc(),
                )
                .limit(10)
            )
            for message in self.db.scalars(candidate_stmt).all():
                legacy_candidates_by_id.setdefault(message.id, message)

        legacy_candidates = sorted(
            (message for message in legacy_candidates_by_id.values()
             if message.created_at is not None),
            key=lambda message: (message.created_at, message.id),
        )

        def _resolve_assistant_msg_id(execution: WorkflowExecution) -> uuid.UUID:
            """Resolve the real assistant Message.id, then synthesize as last resort."""
            execution_id = str(execution.execution_id) if execution.execution_id else ""
            if execution_id in exec_id_to_msg_id:
                return exec_id_to_msg_id[execution_id]
            target = execution.completed_at or execution.started_at
            if target is not None and legacy_candidates:
                best_idx = min(
                    range(len(legacy_candidates)),
                    key=lambda idx: abs(
                        (legacy_candidates[idx].created_at - target).total_seconds()
                    ),
                )
                best_diff = abs(
                    (legacy_candidates[best_idx].created_at - target).total_seconds()
                )
                if best_diff <= match_tolerance.total_seconds():
                    return legacy_candidates.pop(best_idx).id
            return uuid.uuid5(execution.id, "assistant")

        messages: list[AppLogMessage] = []
        node_executions_map: dict[str, list[AppLogNodeExecution]] = {}

        # If an opening statement exists, it is inserted as the first
        # assistant message.
        if opening_msg is not None:
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
            started_at = execution.started_at or utcnow_naive()
            completed_at = execution.completed_at or started_at

            # --- user message (input) ---
            input_data = execution.input_data or {}
            input_content = input_data.get("message") or _extract_text(input_data)

            # Skip executions without user input (e.g. opening-triggered rows)
            # before consuming a legacy assistant-message candidate.
            if not input_content:
                continue

            # The assistant message id doubles as the node_executions_map key;
            # resolving the real Message.id lets the frontend locate the
            # message via the pending_intervention key.
            assistant_msg_id = _resolve_assistant_msg_id(execution)

            files = input_data.get("files") or []
            file_infos = []

            # Locally persisted files only carry upload_file_id (url empty);
            # resolve them to accessible URLs in one batch.
            local_file_url_map: dict[str, str] = {}
            local_file_ids = set()
            for f in files:
                if isinstance(f, dict) and not f.get("url") and f.get("upload_file_id"):
                    try:
                        local_file_ids.add(uuid.UUID(str(f["upload_file_id"])))
                    except (ValueError, TypeError):
                        continue
            if local_file_ids:
                completed_files = self.db.scalars(
                    select(FileMetadata).where(
                        FileMetadata.id.in_(local_file_ids),
                        FileMetadata.workspace_id == workspace_id,
                        FileMetadata.status == "completed",
                    )
                ).all()
                local_file_url_map = {
                    str(f.id): build_permanent_file_url(f.id) for f in completed_files
                }

            resolved_meta_files = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                file_url = f.get("url") or local_file_url_map.get(str(f.get("upload_file_id")))
                if file_url:
                    file_infos.append(LogFileInfo(
                        type=f.get("type", ""),
                        url=file_url,
                        name=f.get("name"),
                        size=f.get("size"),
                        file_type=f.get("file_type"),
                    ))
                    # Inject into the response meta_data.files: the frontend
                    # ChatContent renders directly from this field.
                    resolved_meta_files.append({**f, "url": file_url})

            user_msg = AppLogMessage(
                id=uuid.uuid5(execution.id, "user"),
                conversation_id=conversation_id,
                role="user",
                content=input_content,
                meta_data={"files": resolved_meta_files} if resolved_meta_files else None,
                files=file_infos,
                created_at=started_at,
            )
            messages.append(user_msg)

            # --- assistant message (output) ---
            if execution.status == "completed":
                # Output moderation triggered: use the preset content instead
                # of the raw AI reply.
                if (
                    isinstance(execution.output_data, dict)
                    and execution.output_data.get("moderation_flagged")
                ):
                    output_content = (
                        execution.output_data.get("preset_response", "")
                        or _extract_text(execution.output_data)
                    )
                else:
                    output_content = _extract_text(execution.output_data)
                meta = {
                    "usage": execution.token_usage or {},
                    "elapsed_time": execution.elapsed_time,
                }
                logical_outputs = _extract_workflow_outputs(execution.output_data)
                if logical_outputs:
                    meta["outputs"] = logical_outputs
            elif execution.status == "waiting_human":
                # Paused for human intervention: no output text exists, and the
                # raw output_data JSON (node_outputs etc.) must not leak into
                # content.
                output_content = ""
                intervention_ctx = (execution.context or {}).get("human_intervention", {})
                meta = {
                    "waiting_human": True,
                    "intervention": intervention_ctx,
                    "elapsed_time": execution.elapsed_time,
                }
            elif execution.status == "timeout":
                output_content = execution.error_message or ""
                meta = {
                    "timeout": True,
                    "error_node_id": execution.error_node_id,
                    "elapsed_time": execution.elapsed_time,
                }
            elif execution.status == "cancelled":
                output_content = (
                    _extract_text(execution.output_data) if execution.output_data else ""
                )
                meta = {"cancelled": True, "elapsed_time": execution.elapsed_time}
                logical_outputs = (
                    _extract_workflow_outputs(execution.output_data)
                    if execution.output_data
                    else None
                )
                if logical_outputs:
                    meta["outputs"] = logical_outputs
            else:
                # failed: prefer error_message; never dump output_data.
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

            # --- node execution records from output_data["node_outputs"] ---
            execution_nodes = _build_nodes_from_output_data(execution.output_data)

            if execution_nodes:
                node_executions_map[str(assistant_msg_id)] = execution_nodes

        return (
            messages,
            node_executions_map,
            has_earlier,
            total,
            page_execution_ids,
        )

    def _get_agent_node_executions(
        self,
        conversation_id: uuid.UUID,
        messages: list[Message],
    ) -> dict[str, list[AppLogNodeExecution]]:
        """Read node execution records of an Agent app from
        ``agent_executions``, scoped to one page of messages.

        Multi-agent clusters (approach A): each sub-agent folds into a
        node_type='agent' + agent_log=<trace> node under its message,
        structurally identical to a workflow agent node, so the detail page
        renders them with zero changes.

        Only executions linked to the page's messages (or inside its time
        window) are loaded; paging keeps the JSONB ``steps`` / ``agent_log``
        payloads bounded instead of scanning the whole conversation.

        Returns:
            node executions grouped by message id
        """

        if not messages:
            return {}

        agent_exec_repo = AgentExecutionRepository(self.db)
        page_message_ids = {m.id for m in messages}
        page_times = [m.created_at for m in messages if m.created_at is not None]
        window_start = min(page_times) if page_times else None
        window_end = max(page_times) if page_times else None
        # A legacy execution can run longer than five minutes. Repository
        # scoping also considers completed_at, so matching the page time range
        # remains bounded while long-running traces are retained.
        execution_window_start = window_start

        # Anchors are master records only. Sub-agent records must hang under the
        # message of their master; without the filter they would be treated as
        # standalone executions and stick to other messages. Scoped to the page.
        executions = agent_exec_repo.get_page_scoped(
            conversation_id,
            message_ids=page_message_ids,
            window_start=execution_window_start,
            window_end=window_end,
            agent_role="master",
        )

        parent_ids = {ex.id for ex in executions}
        # Sub records of the page's masters in one query.
        subs_by_parent: dict[str, list] = {}
        for sub in agent_exec_repo.list_sub_by_parents(conversation_id, parent_ids):
            if sub.parent_execution_id is not None:
                subs_by_parent.setdefault(str(sub.parent_execution_id), []).append(sub)

        # Legacy degraded rows can have parent_execution_id=NULL. Keep their
        # lookup page-scoped (direct message ids or the page's five-minute
        # look-back window) instead of loading every sub execution in the chat.
        orphan_subs = agent_exec_repo.list_orphan_sub_page_scoped(
            conversation_id,
            message_ids=page_message_ids,
            window_start=execution_window_start,
            window_end=window_end,
            limit=max(len(page_message_ids) * 20, 100),
        )
        if not executions and not orphan_subs:
            return {}

        node_executions_map: dict[str, list[AppLogNodeExecution]] = {}

        # Assistant messages used for time-based matching of legacy master rows
        # whose message_id backfill is missing.
        assistant_messages = [m for m in messages if m.role == "assistant"]
        used_message_ids: set[str] = set()

        def _match_message(target_at, ignore_used: bool = False) -> str | None:
            """Snap one execution's completion/start time to the next assistant message.

            ignore_used=True lifts the "already taken" restriction: concurrent
            sub-agents of one round share the same assistant message, otherwise
            every sub-agent but the first would be dropped (2026-09-14).

            Returns the message id string, or None without a candidate.
            """
            if not target_at:
                return None
            best_msg = None
            best_dt = None
            for msg in assistant_messages:
                msg_id_str = str(msg.id)
                if not ignore_used and msg_id_str in used_message_ids:
                    continue
                if msg.created_at and msg.created_at >= target_at:
                    delta = (msg.created_at - target_at).total_seconds()
                    if best_dt is None or delta < best_dt:
                        best_dt = delta
                        best_msg = msg
            if best_msg is None or best_dt is None:
                return None
            used_message_ids.add(str(best_msg.id))
            return str(best_msg.id)

        # Resolve which page message each master record lands on.
        # message_id wins; otherwise time-based snapping within the page.
        key_by_execution: dict[str, str] = {}
        for execution in executions:
            steps = execution.steps or []
            child_nodes: list[AppLogNodeExecution] = self._build_child_nodes(
                subs_by_parent.get(str(execution.id), [])
            )
            execution_nodes = _steps_to_node_executions(steps) if steps else []
            # Supervisor's own trace (direct_answer has no steps/sub rows, but
            # agent_log carries its LLM turns and tools)
            master_node = self._master_execution_to_node(execution)
            if master_node is not None:
                execution_nodes.append(master_node)

            if not execution_nodes and not child_nodes:
                continue

            key = (
                str(execution.message_id)
                if execution.message_id and execution.message_id in page_message_ids
                else _match_message(execution.completed_at or execution.started_at)
            )
            if not key:
                continue

            key_by_execution[str(execution.id)] = key
            # Sub-agent nodes append to the same message's node list.
            node_executions_map.setdefault(key, []).extend(execution_nodes + child_nodes)

        # If the master record failed to persist, retain orphan sub-agent traces
        # by attaching them to the nearest page master, or directly to the
        # nearest assistant message when no master exists on this page.
        for sub in orphan_subs:
            target_key = None
            target_id = None
            if sub.message_id and sub.message_id in page_message_ids:
                target_key = str(sub.message_id)
            elif sub.completed_at or sub.started_at:
                sub_target_at = sub.completed_at or sub.started_at
                best_master = None
                best_delta = None
                for execution in executions:
                    execution_target_at = execution.completed_at or execution.started_at
                    if not execution_target_at or sub_target_at < execution_target_at:
                        continue
                    delta = (sub_target_at - execution_target_at).total_seconds()
                    if delta <= 300 and (best_delta is None or delta < best_delta):
                        best_master = execution
                        best_delta = delta
                if best_master is not None:
                    target_id = str(best_master.id)
                    target_key = key_by_execution.get(target_id)
            if target_key is None:
                target_key = _match_message(
                    sub.completed_at or sub.started_at, ignore_used=True
                )
            if target_key is None:
                continue

            node = self._sub_execution_to_node(sub)
            if target_id:
                node.parent_execution_id = target_id
                node.meta = {**(node.meta or {}), "parent_execution_id": target_id}
            node_executions_map.setdefault(target_key, []).append(node)

        return node_executions_map

    def _build_child_nodes(self, subs: list) -> list[AppLogNodeExecution]:
        """Project a group of sub-agent executions into nodes (one agent node
        per record).

        Records without a trace (e.g. collaboration-mode node activations)
        additionally unfold their non-agent steps (tool calls / handoff) so the
        call sequence stays visible; agent-type steps already hang on the agent
        node itself and unfolding them would duplicate the node.
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
        """Fold one sub-agent execution into a node_type='agent' node.

        Structurally identical to a workflow agent node: agent_log carries the
        trace (ROUND / llm / tool_calls), input/output/elapsed_time/token_usage
        are filled as usual — the frontend's existing agent branch renders it
        without any change.

        Without a trace it degrades to {"meta": ..., "iterations": []}: the
        node still opens and shows input/output and status instead of
        disappearing when observability data is missing.
        """

        meta = sub.meta_data if isinstance(sub.meta_data, dict) else {}
        steps = sub.steps or []
        agent_name = meta.get("agent_name") or "子 Agent"

        trace = sub.agent_log
        # Legacy/fallback path: the trace used to live in meta_data.agent_log.
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
        # Collaboration-mode steps interleave handoff steps: the node's own
        # input/output must come from agent-type steps.
        agent_steps = [s for s in steps if isinstance(s, dict) and s.get("node_type") == "agent"]
        if agent_steps:
            first_step = agent_steps[0]
            last_step = agent_steps[-1]

        # Input: prefer the dispatched task (the sub-question the agent
        # received), not tool arguments — supervisor-mode steps only carry tool
        # steps and first_step would show `{"query": "..."}`.
        input_payload = meta.get("task") or (first_step.get("input") if first_step else None)
        # Output: the last LLM answer in the trace is the deliverable; degrade
        # to the last tool result only when absent.
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
                "parent_execution_id": (
                    str(sub.parent_execution_id) if sub.parent_execution_id else None
                ),
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

    @staticmethod
    def _master_execution_to_node(execution) -> AppLogNodeExecution | None:
        """Project the cluster master (orchestrator) execution itself into one
        node_type='agent' node.

        In supervisor_loop the supervisor may answer directly with zero
        dispatches (direct_answer): there are no sub-agent rows, and the
        supervisor's own LLM turns / tool calls live only in its agent_log.
        Without this node, version-switch / log-detail views rebuilt from
        agent_executions lose the supervisor tool-call panel. Returns None when
        there is no usable trace (no iterations).
        """
        meta = execution.meta_data if isinstance(execution.meta_data, dict) else {}
        trace = getattr(execution, "agent_log", None)
        if not (isinstance(trace, dict) and trace.get("iterations")):
            legacy = meta.get("agent_log")
            if isinstance(legacy, dict) and legacy.get("iterations"):
                trace = legacy
        if not (isinstance(trace, dict) and trace.get("iterations")):
            return None
        agent_name = meta.get("agent_name") or "主编排"
        return AppLogNodeExecution(
            node_id=str(execution.id),
            node_type="agent",
            node_name=agent_name,
            status=execution.status or "completed",
            error=execution.error_message,
            input=meta.get("task"),
            output=_trace_final_output(trace),
            agent_log=trace,
            elapsed_time=execution.elapsed_time,
            token_usage=execution.token_usage,
            meta={
                "agent_id": meta.get("agent_id"),
                "agent_name": agent_name,
                "execution_id": str(execution.id),
                "parent_execution_id": None,
                "depth": 0,
                "orchestration_mode": getattr(execution, "orchestration_mode", None),
            },
            agent_id=meta.get("agent_id"),
            agent_name=agent_name,
            execution_id=str(execution.id),
            parent_execution_id=None,
            depth=0,
            orchestration_mode=getattr(execution, "orchestration_mode", None),
        )

    def build_agent_execution_summary(
        self,
        node_executions_map: dict[str, list[AppLogNodeExecution]],
    ) -> dict[str, list[AppLogAgentSummary]]:
        """Per assistant message_id: shallow overview of the cluster round.

        Data-only entry: how many agents, each status/elapsed/token, tool call
        counts, iteration counts. Not rendered this iteration; future cluster
        overview cards / swimlane views consume it as-is.
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
        """Sub-agent execution count per conversation; failures degrade to an
        empty map (never block the log list)."""

        try:
            return AgentExecutionRepository(self.db).count_sub_by_conversations(conversation_ids)
        except Exception as e:
            logger.warning("Failed to count sub-agent executions (degraded to 0): %s", e)
            return {}

    def get_message_node_executions(self, message_id: uuid.UUID) -> list[dict]:
        """Node execution details of one assistant message (agent tool-call trail).

        Backfills subContent for version switches: match by
        agent_executions.message_id first; when missing, fall back to the
        nearest completed execution within 300s (same policy as the workflow
        branch view). Returns an empty list when no execution is linked.

        Multi-agent clusters: match master records only (sub records would be
        time-absorbed into the main message); once matched, the master's
        sub-agent nodes are expanded as well.

        Regenerated versions run the LLM with skip_save=True and persist no
        agent_execution, so they have no trail to return.
        """

        message = self.db.get(Message, message_id)
        if not message or message.role != "assistant":
            return []
        # Opening statements and similar non-execution assistant messages carry
        # no execution trace in meta_data (execution_id / regenerated_from /
        # usage); skip the time fallback so they are not absorbed into a
        # neighbouring execution.
        meta = message.meta_data if isinstance(message.meta_data, dict) else None
        if meta and not any(k in meta for k in ("execution_id", "regenerated_from", "usage")):
            return []

        agent_exec_repo = AgentExecutionRepository(self.db)
        execution = agent_exec_repo.get_by_message_id(message.id, agent_role="master")

        # Fallback: the first-round reply predates message_id backfill on
        # agent_execution, so match by nearest completion time instead.
        if not execution and message.created_at:
            target = message.created_at
            best = None
            best_diff = None
            master_execs = agent_exec_repo.get_by_conversation(
                message.conversation_id, agent_role="master"
            )
            for ex in master_execs:
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
        # Supervisor's own trace (direct_answer has no steps/sub rows, but
        # agent_log carries its LLM turns and tools)
        master_node = self._master_execution_to_node(execution)
        if master_node is not None:
            nodes = nodes + [master_node]
        if not nodes and not child_nodes:
            return []
        return [n.model_dump() for n in (nodes + child_nodes)]


def _format_log_json(data: Any) -> str:
    """Render any workflow input/output as readable JSON in log messages."""

    try:
        return json.dumps(
            data if data is not None else {}, ensure_ascii=False, indent=2, default=str
        )
    except (TypeError, ValueError):
        return str(data or "")


def _extract_execution_output(data: Any) -> str:
    """Extract the pure workflow final output; keep complex structures as
    formatted JSON."""

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


def _extract_text(data: dict | None) -> str:
    """Extract readable text from workflow execution input_data / output_data.

    Prefers 'text', 'content', 'output' fields (strings or stringifiable
    values); returns an empty string otherwise (never dumps the whole dict as
    JSON, which would leak node_outputs etc. into assistant content).
    """

    if not data:
        return ""
    for key in ("message", "text", "content", "output", "result", "answer"):
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            return val
        # output_data["output"] may be the End node's output string.
        if key == "output" and val is not None:
            return str(val)
    return ""


def _extract_workflow_outputs(data: dict | None) -> list[dict]:
    """Return logical Answer/End outputs without flattening them together.

    New executions store ``output_data.outputs`` directly; the fallback reads
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


def _build_nodes_from_output_data(output_data: dict | None) -> list[AppLogNodeExecution]:
    """Build node execution records from
    ``workflow_executions.output_data["node_outputs"]``.

    output_data shape:
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
    # Sort by execution_order (the monotonic sequence written at node
    # execution). PostgreSQL JSONB does not preserve key order, so dict
    # insertion order cannot be trusted; legacy rows without execution_order
    # degrade to 0 and stay first.
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
        # Strip the known top-level fields; the rest becomes output.
        node_type = output.pop("node_type", "unknown")
        node_name = output.pop("node_name", None)
        status = output.pop("status", "completed")
        error = output.pop("error", None)
        inp = output.pop("input", None)
        elapsed_time = output.pop("elapsed_time", None)
        token_usage = output.pop("token_usage", None)
        process = output.pop("process", None)
        agent_log = output.pop("agent_log", None)
        # execution_order only orders the sort and is not returned.
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
    """Whether the execution carries a renderable trace (new agent_log column,
    legacy meta_data.agent_log)."""

    trace = getattr(execution, "agent_log", None)
    if isinstance(trace, dict) and trace.get("iterations"):
        return True
    meta = getattr(execution, "meta_data", None)
    if isinstance(meta, dict):
        legacy = meta.get("agent_log")
        if isinstance(legacy, dict) and legacy.get("iterations"):
            return True
    return False


def _trace_final_output(trace: dict | None) -> str | None:
    """Extract the sub-agent's "final deliverable" from its trace.

    The trace is ``{meta, iterations:[{llm:{...}, tool_calls:[...]}]}``.
    A sub-agent's steps only hold tool steps (web_search etc.), so
    input/output cannot come from there (that would display a tool argument /
    result); the real deliverable is its last LLM answer. Scan iterations in
    reverse for the first non-empty ``llm.output`` (the last round usually has
    no tool_calls, so its output is the final draft).
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


def _steps_to_node_executions(steps: list | None) -> list[AppLogNodeExecution]:
    """Convert agent_executions.steps (JSONB array) into AppLogNodeExecution.

    Step shape: {step_id, node_type, node_name, status, input, output,
    elapsed_time, error, meta}; the output is structurally identical to
    _build_nodes_from_output_data (workflow side), so the frontend renders both
    through the same subContent path.
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


__all__ = ["AppLogService"]
