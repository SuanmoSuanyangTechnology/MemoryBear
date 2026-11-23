"""Session-bound ownership/configuration reads shared by task repositories."""

from uuid import UUID

from sqlalchemy import func, select

from src.application.ports.task_context import TaskScope
from src.domain.shared.ids import ModelConfigId, parse_uuid
from src.infrastructure.persistence.postgres.model_config_repository import (
    SqlAlchemyModelConfigRepository,
)
from src.models import EndUser, MemoryConfigRow, Workspace


class SqlAlchemyTaskContextRepository:
    def __init__(self, session) -> None:
        self.session = session

    def context(self, scope: TaskScope) -> dict:
        payload = scope.payload()
        wid, tid = scope.workspace_id, scope.tenant_id
        session = self.session
        workspace = session.execute(
            select(Workspace).where(
                Workspace.id == wid,
                Workspace.tenant_id == tid,
                Workspace.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if workspace is None:
            raise ValueError("工作空间不存在或不可访问")
        uid = scope.end_user_id
        if (
            session.scalar(
                select(EndUser.id).where(
                    EndUser.id == uid,
                    EndUser.workspace_id == wid,
                    EndUser.is_active.is_(True),
                )
            )
            is None
        ):
            raise ValueError("终端用户不存在或不属于当前工作空间")
        config_id = payload.get("config_id")
        predicate = MemoryConfigRow.config_id == workspace.memory_config
        if config_id is not None:
            try:
                predicate = MemoryConfigRow.config_id == UUID(str(config_id))
            except ValueError:
                try:
                    predicate = MemoryConfigRow.config_id_old == int(config_id)
                except (ValueError, TypeError) as exc:
                    raise ValueError("无效配置ID") from exc
        config = session.execute(
            select(MemoryConfigRow).where(
                predicate,
                MemoryConfigRow.workspace_id == wid,
            )
        ).scalar_one_or_none()
        if config is None:
            raise ValueError("记忆配置不存在或不属于当前工作空间")
        # Resolve model with existing slot/preset logic but without the
        # graph-only profile-cache eligibility guard (RAG needs this too).
        if config.config_id != workspace.memory_config:
            raise ValueError("任务配置已不再是当前工作空间的激活配置")
        model = SqlAlchemyModelConfigRepository(session).resolve_model(
            config_id=ModelConfigId(config.config_id),
            slot="llm",
            is_default_config=bool(workspace.is_default_config),
            workspace_model_id=parse_uuid(workspace.llm),
        )
        model_id = None
        if model and model.model_id and model.model_row_id and model.is_active:
            if model.is_public or (model.tenant_id and model.tenant_id.value == tid):
                model_id = str(model.model_id.value)
        return {
            "workspace_id": str(wid),
            "tenant_id": str(tid),
            "end_user_id": str(uid),
            "config_id": str(config.config_id),
            "requested_config_id": config_id,
            "config_updated_at": config.updated_at,
            "storage_type": workspace.storage_type or "neo4j",
            "model_id": model_id,
            "actor_id": str(payload["actor_id"]) if payload.get("actor_id") else None,
            "enable_llm_summary": bool(config.enable_llm_summary),
            "forgetting_threshold": float(
                config.forgetting_threshold if config.forgetting_threshold is not None else 0.3
            ),
        }

    @staticmethod
    def guard(session, context, *, require_config: bool = True) -> None:
        # Row locks last only for the short write transaction, never LLM I/O.
        workspace = session.execute(
            select(Workspace.id)
            .where(
                Workspace.id == UUID(context["workspace_id"]),
                Workspace.tenant_id == UUID(context["tenant_id"]),
                Workspace.is_active.is_(True),
                func.coalesce(Workspace.storage_type, "neo4j") == context["storage_type"],
                *(
                    [Workspace.memory_config == UUID(context["config_id"])]
                    if require_config
                    else []
                ),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if workspace is None:
            raise ValueError("工作空间或记忆配置已变更，拒绝保存过期结果")
        if require_config:
            config = session.scalar(
                select(MemoryConfigRow.config_id)
                .where(
                    MemoryConfigRow.config_id == UUID(context["config_id"]),
                    MemoryConfigRow.workspace_id == UUID(context["workspace_id"]),
                    MemoryConfigRow.updated_at.is_not_distinct_from(context["config_updated_at"]),
                )
                .with_for_update()
            )
            if config is None:
                raise ValueError("任务执行期间记忆配置已变更")
        user = session.scalar(
            select(EndUser.id)
            .where(
                EndUser.id == UUID(context["end_user_id"]),
                EndUser.workspace_id == UUID(context["workspace_id"]),
                EndUser.is_active.is_(True),
            )
            .with_for_update()
        )
        if user is None:
            raise ValueError("终端用户已失效或不属于当前工作空间")

    def check(self, context, *, require_config: bool = True) -> None:
        self.guard(self.session, context, require_config=require_config)
