"""
Workflow service.

Covers the config preparation needed by the definition-plane write paths
(``_prepare_workflow_config_dict``), file-variable default resolution before
workflow draft updates (``_resolve_variables_file_defaults``), and DSL export
(``export_workflow_dsl``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cache.workflow_cache import delete_workflow_config
from ..config import current_settings
from ..errors import BizCode, BusinessException
from ..models.app_model import App
from ..models.conversation_model import Message
from ..models.file_metadata_model import FileMetadata
from ..models.message_feedback_model import MessageFeedback
from ..models.workflow_model import WorkflowConfig, WorkflowExecution
from ..repositories.workflow_repository import (
    WorkflowConfigRepository,
    WorkflowExecutionRepository,
    WorkflowNodeExecutionRepository,
)
from ..schemas.app_log_schema import AppLogNodeExecution
from ..schemas.app_schema import FileType
from ..utils.datetime_utils import to_timestamp_ms
from ..utils.file_processor import build_file_object_dict_from_meta, fetch_remote_file_meta
from ..utils.workflow_node_types import NodeType
from ..utils.workflow_triggers import TRIGGER_NODES_PREPARED_FLAG, normalize_trigger_nodes
from ..utils.workflow_validator import WorkflowValidator
from .app_log_service import AppLogService, _build_nodes_from_output_data

logger = logging.getLogger(__name__)


class WorkflowService:
    """Workflow business logic (definition-plane write subset)."""

    def __init__(self, db: Session):
        self.db = db
        self.config_repo = WorkflowConfigRepository(db)
        self.execution_repo = WorkflowExecutionRepository(db)
        self.node_execution_repo = WorkflowNodeExecutionRepository(db)

    @staticmethod
    def _prepare_nodes(
        nodes: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        try:
            return normalize_trigger_nodes(nodes)
        except ValueError as exc:
            raise BusinessException(
                code=BizCode.INVALID_PARAMETER,
                message=f"工作流触发器配置无效: {exc}",
            ) from exc

    @staticmethod
    def _prepare_triggers(
        triggers: list[dict[str, Any]] | None,
        _nodes: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        return triggers or []

    def _prepare_workflow_config_dict(
        self,
        *,
        nodes: list[dict[str, Any]] | None,
        edges: list[dict[str, Any]] | None,
        variables: list[dict[str, Any]] | None,
        environment_variables: list[dict[str, Any]] | None,
        execution_config: dict[str, Any] | None,
        features: dict[str, Any] | None,
        triggers: list[dict[str, Any]] | None,
        workflow_type: str | None,
    ) -> dict[str, Any]:
        normalized_nodes = self._prepare_nodes(nodes)
        normalized_triggers = self._prepare_triggers(triggers, normalized_nodes)
        return {
            "nodes": normalized_nodes,
            "edges": edges or [],
            "variables": variables or [],
            "environment_variables": environment_variables or [],
            "execution_config": execution_config or {},
            "features": features or {},
            "triggers": normalized_triggers,
            "workflow_type": workflow_type or "workflow",
            TRIGGER_NODES_PREPARED_FLAG: True,
        }

    def _resolve_variables_file_defaults(
        self,
        variables: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert FileInput-format defaults in workflow variables to full FileObject dicts."""

        def _resolve_one(item: dict) -> dict | None:
            if not isinstance(item, dict) or item.get("is_file"):
                return item
            transfer_method = item.get("transfer_method", "remote_url")
            file_type = FileType.trans(item.get("type", "document"))
            origin_file_type = item.get("file_type") or file_type
            if transfer_method == "remote_url":
                url = item.get("url", "")
                return (
                    fetch_remote_file_meta(url, file_type, origin_file_type)
                    if url
                    else None
                )
            else:
                return self._resolve_local_file(
                    item.get("upload_file_id"), file_type, origin_file_type
                )

        result = []
        for var_def in variables:
            var_type = var_def.get("type", "")
            default = var_def.get("default")
            if var_type == "file" and isinstance(default, dict) and not default.get("is_file"):
                var_def = {**var_def, "default": _resolve_one(default)}
            elif var_type == "array[file]" and isinstance(default, list):
                resolved = []
                for item in default:
                    r = _resolve_one(item)
                    if r is not None:
                        resolved.append(r)
                var_def = {**var_def, "default": resolved}
            result.append(var_def)
        return result

    def _resolve_local_file(
        self,
        upload_file_id,
        file_type: str,
        origin_file_type: str,
    ) -> dict | None:
        """Synchronous version of resolve_local_file_object_dict (admin-plane write path)."""
        try:
            fid = uuid.UUID(str(upload_file_id))
        except ValueError:
            return None

        meta = self.db.query(FileMetadata).filter(
            FileMetadata.id == fid,
            FileMetadata.status == "completed",
        ).first()
        if not meta:
            return None

        url = f"{current_settings().file_local_server_url}/storage/permanent/{fid}"
        return build_file_object_dict_from_meta(
            file_type=file_type,
            transfer_method="local_file",
            origin_file_type=origin_file_type,
            file_id=str(fid),
            url=url,
            file_name=meta.file_name,
            file_size=meta.file_size,
            file_ext=meta.file_ext,
            content_type=meta.content_type,
        )

    def create_workflow_config(
            self,
            *,
            app_id: uuid.UUID,
            nodes: list[dict[str, Any]],
            edges: list[dict[str, Any]],
            variables: list[dict[str, Any]] | None = None,
            environment_variables: list[dict[str, Any]] | None = None,
            execution_config: dict[str, Any] | None = None,
            features: dict[str, Any] | None = None,
            triggers: list[dict[str, Any]] | None = None,
            workflow_type: str = "workflow",
            validate: bool = True,
            redis_client: Any
    ) -> WorkflowConfig:
        """Create a workflow config.

        Args:
            app_id: App ID
            nodes: Node list
            edges: Edge list
            variables: Variable list
            execution_config: Execution config
            features: Feature flags
            triggers: Trigger list
            workflow_type: Workflow type
            validate: Whether to validate the config
            redis_client: Shared-cache client for the cross-process invalidation.

        Returns:
            The workflow config

        Raises:
            BusinessException: When the config is invalid
        """
        config_dict = self._prepare_workflow_config_dict(
            nodes=nodes,
            edges=edges,
            variables=variables,
            environment_variables=environment_variables,
            execution_config=execution_config,
            features=features,
            triggers=triggers,
            workflow_type=workflow_type,
        )
        normalized_nodes = config_dict["nodes"]
        normalized_triggers = config_dict["triggers"]

        # Validate the config
        if validate:
            is_valid, errors = WorkflowValidator.validate(config_dict)
            if not is_valid:
                logger.warning(f"工作流配置验证失败: {errors}")
                raise BusinessException(
                    code=BizCode.INVALID_PARAMETER,
                    message=f"工作流配置无效: {'; '.join(errors)}"
                )

        # Create or update the config
        config = self.config_repo.create_or_update(
            app_id=app_id,
            nodes=normalized_nodes,
            edges=edges,
            variables=variables,
            environment_variables=environment_variables,
            execution_config=execution_config,
            features=features,
            triggers=normalized_triggers,
            workflow_type=workflow_type
        )
        delete_workflow_config(redis_client, app_id)

        logger.info(f"创建工作流配置成功: app_id={app_id}, config_id={config.id}")
        return config

    def get_workflow_config(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """Get the workflow config (ORM row, for the update path)."""
        return self.config_repo.get_by_app_id(app_id)

    def _resolve_parent_user_message(self, msg) -> Message | None:
        """Read-path locator of an assistant message's parent user message
        (None instead of raising).

        Same policy as the write path: prefer ``parent_message_id`` when it
        points at a user row; otherwise walk back to the nearest earlier user
        message. Historic assistant messages from the intervention-pause path
        carry no parent link and rely on this fallback.
        """

        if msg.parent_message_id:
            parent = self.db.get(Message, msg.parent_message_id)
            if parent and parent.role == "user":
                return parent
        if msg.created_at is None:
            return None
        return self.db.scalars(
            select(Message)
            .where(
                Message.conversation_id == msg.conversation_id,
                Message.role == "user",
                Message.created_at < msg.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        ).first()

    def _load_branch_chain(self, message_id) -> list:
        """Load the full linear chain through ``message_id`` (ancestors above,
        descendants below); root first.

        Descends along the ``is_current`` child (newest child when none is
        flagged).
        """

        try:
            mid = uuid.UUID(message_id) if isinstance(message_id, str) else message_id
            msg = self.db.get(Message, mid)
        except (ValueError, TypeError):
            return []
        if not msg:
            return []

        # Upward: ancestor chain (root to msg, msg excluded). A missing
        # parent_message_id on an assistant row falls back to the nearest
        # earlier user message (the intervention-pause path leaves historic
        # assistant rows unlinked; without the fallback the turn's user
        # message would be lost).
        ancestors: list[Message] = []
        visited_up: set = {msg.id}
        cur = msg
        while cur:
            nxt: Message | None = None
            if cur.parent_message_id and cur.parent_message_id not in visited_up:
                parent = self.db.get(Message, cur.parent_message_id)
                if parent:
                    nxt = parent
            elif not cur.parent_message_id and cur.role == "assistant":
                fb = self._resolve_parent_user_message(cur)
                if fb and fb.id not in visited_up:
                    nxt = fb
            if nxt is None:
                break
            visited_up.add(nxt.id)
            ancestors.append(nxt)
            cur = nxt
        ancestors.reverse()

        # Downward: descendant chain. Prefer children linked by
        # parent_message_id; when an agent-historic user message has no parent
        # link, fall back to the next unlinked user message by time (only then
        # — a user row pointing at another assistant belongs to another
        # branch).
        descendants: list[Message] = []
        visited_down: set = {msg.id}
        cur = msg
        while True:
            child = self.db.query(Message).filter(
                Message.parent_message_id == cur.id,
                Message.is_current.is_(True),
            ).order_by(Message.created_at.desc()).first()
            if child is None:
                child = self.db.query(Message).filter(
                    Message.parent_message_id == cur.id,
                ).order_by(Message.created_at.desc()).first()
            if child is None and cur.created_at is not None and cur.role == "assistant":
                child = self.db.query(Message).filter(
                    Message.conversation_id == cur.conversation_id,
                    Message.is_deleted.is_not(True),
                    Message.created_at > cur.created_at,
                    Message.role == "user",
                    Message.parent_message_id.is_(None),
                ).order_by(Message.created_at.asc()).first()
            if child is None or child.id in visited_down:
                break
            visited_down.add(child.id)
            descendants.append(child)
            cur = child

        return ancestors + [msg] + descendants

    def _resolve_sibling_versions(self, m) -> list:
        """All versions of the assistant turn ``m`` belongs to (``m``
        included), ascending by version.

        A turn is anchored on one parent user message. Versions linked by
        ``parent_message_id`` aggregate exactly; a parentless historic
        first-round reply (intervention-pause path) is resolved through the
        [parent_user, next_user) time window so it groups with its regenerated
        siblings. ``m`` itself always lands in the result.
        """

        parent_user = self._resolve_parent_user_message(m)
        if parent_user is None:
            return [m]
        next_user = None
        if parent_user.created_at is not None:
            next_user = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == m.conversation_id,
                    Message.role == "user",
                    Message.created_at > parent_user.created_at,
                    Message.is_deleted.is_not(True),
                )
                .order_by(Message.created_at.asc())
                .limit(1)
            ).first()
        # Primary set: rows whose parent points at this turn's user message
        # (no time window — the parent link already pins the turn, and a
        # regenerated version created after next_user would be dropped by the
        # window).
        siblings = self.db.query(Message).filter(
            Message.conversation_id == m.conversation_id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
            Message.parent_message_id == parent_user.id,
        ).order_by(Message.version.asc()).all()
        existing_ids = {str(s.id) for s in siblings}
        # Complement set: parentless legacy first rounds, pinned to this turn
        # by the time window instead.
        orphan_q = self.db.query(Message).filter(
            Message.conversation_id == m.conversation_id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
            Message.parent_message_id.is_(None),
        )
        if parent_user.created_at is not None:
            orphan_q = orphan_q.filter(Message.created_at >= parent_user.created_at)
        if next_user is not None:
            orphan_q = orphan_q.filter(Message.created_at < next_user.created_at)
        for o in orphan_q.order_by(Message.version.asc()).all():
            if str(o.id) not in existing_ids:
                siblings.append(o)
                existing_ids.add(str(o.id))
        if str(m.id) not in existing_ids:
            siblings.append(m)
        siblings.sort(key=lambda s: (s.version or 0))
        return siblings

    def _build_branch_view(self, message_id, user_id: str | None = None) -> dict:
        """Branch view of ``message_id``: the full chain plus per-version node
        execution details.

        Returns {
          messages: flat branch chain (ChatItem | ChatItem[])[] matching the
                    front-end chatList shape — one entry per turn; multi-version
                    assistant turns are sub-arrays, single ones flatten to
                    objects.
          node_executions_map: { <assistant_message_id>: [node detail, ...] }
                    lets the front end rebuild subContent after a refresh.
          pending_intervention: { <message_id>: {execution_id, status,
                    interventions} } reusing the log system's aggregation.
        }
        """

        chain = self._load_branch_chain(message_id)
        if not chain:
            return {"messages": [], "node_executions_map": {}, "pending_intervention": {}}

        def serialize(m: Message) -> dict:
            fb_type, fb_content = feedback_map.get(m.id, (None, None))
            return {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "version": m.version,
                "is_current": m.is_current,
                "created_at": to_timestamp_ms(m.created_at),
                "meta_data": m.meta_data,
                "status": m.status,
                "parent_message_id": str(m.parent_message_id) if m.parent_message_id else None,
                "is_favorited": m.id in favorited_ids,
                "feedback_type": fb_type,
                "feedback_content": fb_content,
            }

        # Preload every execution of the conversation for the time-based
        # fallback (the original first-run assistant message carries no
        # execution_id in meta_data, unlike regenerated ones).
        conv_id = chain[0].conversation_id
        fallback_executions = self.db.query(WorkflowExecution).filter(
            WorkflowExecution.conversation_id == conv_id,
            WorkflowExecution.status.in_(
                ["completed", "failed", "waiting_human", "timeout", "cancelled"]
            ),
        ).all() if conv_id else []

        def _find_execution_by_time(target_msg: Message):
            """Nearest completion-time match (300s window, same as the log
            system fallback)."""

            target = target_msg.created_at
            best = None
            best_diff = None
            for ex in fallback_executions:
                ref = ex.completed_at or ex.started_at
                if not ref:
                    continue
                diff = abs((ref - target).total_seconds())
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = ex
            if best is not None and best_diff is not None and best_diff <= 300:
                return best
            return None

        def build_nodes(m: Message) -> list[dict]:
            """Node details of one version, reusing the log serialization.

            Prefer the meta_data.execution_id lookup, fall back to the nearest
            completion time (the original first round has no execution_id).
            When no WorkflowExecution matches (agent apps persist
            AgentExecution instead), fall back to
            AppLogService.get_message_node_executions so version switches of
            agent trial runs still backfill subContent.
            """

            meta = m.meta_data if isinstance(m.meta_data, dict) else None
            # Opening statements and similar non-execution assistant messages
            # carry no execution trace (execution_id / regenerated_from /
            # usage); skip the time fallback so they are not absorbed into a
            # neighbouring execution.
            if meta and not any(k in meta for k in ("execution_id", "regenerated_from", "usage")):
                return []
            exec_id = meta.get("execution_id") if meta else None
            if exec_id:
                execution = self.execution_repo.get_by_execution_id(exec_id)
            else:
                execution = _find_execution_by_time(m)
            if execution:
                # The persisted WorkflowNodeExecution rows hold the final
                # state (written by _persist_workflow_node_executions); HITL
                # nodes left as waiting in output_data are corrected here.
                if execution.id:
                    tbl_nodes = self.node_execution_repo.get_by_execution_id(execution.id)
                else:
                    tbl_nodes = []
                if tbl_nodes:
                    ordered_nodes = sorted(tbl_nodes, key=lambda n: n.execution_order or 0)
                    result = []
                    for n in ordered_nodes:
                        process_data = None
                        if isinstance(n.meta_data, dict):
                            process_data = n.meta_data.get("process_data")
                        result.append(AppLogNodeExecution(
                            node_id=n.node_id,
                            node_type=n.node_type or "unknown",
                            node_name=n.node_name,
                            status=n.status or "completed",
                            error=n.error_message,
                            input=n.input_data,
                            process=process_data,
                            output=n.output_data,
                            elapsed_time=n.elapsed_time,
                            token_usage=n.token_usage,
                            meta=n.meta_data,
                        ))
                    if result:
                        return [n.model_dump() for n in result]
                # No table rows: fall back to output_data (waiting_human
                # before resume).
                if execution.output_data:
                    nodes = _build_nodes_from_output_data(execution.output_data)
                    if nodes:
                        return [n.model_dump() for n in nodes]
            # Agent fallback: agent apps persist AgentExecution.steps with no
            # WorkflowExecution (regenerated versions skip saving and return []
            # — a known limitation).
            return AppLogService(self.db).get_message_node_executions(m.id)

        # Resolve every turn first (assistant turns gather all sibling
        # versions), then batch-query the current user's favorites.
        turns: list[tuple[str, Any]] = []
        all_ids: set = set()
        for m in chain:
            if m.is_deleted:
                continue
            if m.role == "user":
                turns.append(("user", m))
                all_ids.add(m.id)
            elif m.role == "assistant":
                siblings = self._resolve_sibling_versions(m)
                turns.append(("assistant", siblings))
                for s in siblings:
                    all_ids.add(s.id)

        favorited_ids: set = set()
        if user_id and all_ids:
            favorited_ids = {
                row[0]
                for row in self.db.query(MessageFeedback.message_id)
                .filter(
                    MessageFeedback.message_id.in_(all_ids),
                    MessageFeedback.user_id == user_id,
                    MessageFeedback.is_favorite.is_(True),
                )
                .all()
            }

        # Preload the current user's feedback (any-row lookups would leak
        # another user's state into the display).
        feedback_map: dict = {}
        if user_id and all_ids:
            feedback_rows = (
                self.db.query(
                    MessageFeedback.message_id,
                    MessageFeedback.feedback_type,
                    MessageFeedback.feedback_content,
                )
                .filter(
                    MessageFeedback.message_id.in_(all_ids),
                    MessageFeedback.user_id == user_id,
                )
                .all()
            )
            feedback_map = {row[0]: (row[1], row[2]) for row in feedback_rows}

        items: list[Any] = []
        node_executions_map: dict[str, list[dict]] = {}
        for role, payload in turns:
            if role == "user":
                items.append(serialize(payload))
            else:
                # All assistant versions of the turn (for the version switcher)
                # grouped by parent user, covering parentless legacy first
                # rounds.
                siblings = payload
                serialized = [serialize(s) for s in siblings]
                # Single version flattens to an object, multiple stay an array
                # — the front-end chatList convention.
                items.append(serialized[0] if len(serialized) == 1 else serialized)
                # Build node details per version so switching to any version
                # backfills subContent.
                for s in siblings:
                    nodes = build_nodes(s)
                    if nodes:
                        node_executions_map[str(s.id)] = nodes
        pending_intervention = AppLogService(self.db).build_pending_intervention_map(conv_id)
        return {
            "messages": items,
            "node_executions_map": node_executions_map,
            "pending_intervention": pending_intervention,
        }

    @staticmethod
    def get_start_node_variables(config: dict) -> list:
        """Start-node variables of a workflow config ([] without a start node)."""

        nodes = config.get("nodes", [])
        for node in nodes:
            if node.get("type") == NodeType.START:
                return node.get("config", {}).get("variables", [])
        return []

    def export_workflow_dsl(self, app_id: uuid.UUID):
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        app: App = config.app
        dsl_info = {
            "app": {
                "name": app.name,
                "description": app.description,
                "icon": app.icon,
                "icon_type": app.icon_type
            },
            "workflow": {
                "variables": config.variables,
                "edges": config.edges,
                "nodes": config.nodes,
                "execution_config": config.execution_config,
                "triggers": config.triggers
            }
        }
        return yaml.dump(dsl_info, default_flow_style=False, allow_unicode=True)


__all__ = ["WorkflowService"]
