"""Session-less, side-effect-free runner for published Agent apps.

This service resolves the Agent's AppRelease snapshot and reuses AgentRunService
in stateless sub-agent mode. It does not create a Conversation and writes no
Message/AgentExecution records.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, AsyncIterator, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BizCode
from ..errors import BusinessException
from ..db import get_async_db_context
from ..models import App, AppRelease, ModelConfig
from ..models.app_model import AppStatus, AppType
from ..schemas.app_schema import FileInput
from .draft_run_service import AgentRunService
from .model_service import ModelConfigService
from ..utils.app_config_utils import (
    agent_config_4_app_release,
    multi_agent_config_4_app_release,
)

ReleasePolicy = Literal["current", "pinned"]
_MAX_INVOCATION_DEPTH = 5
_INVOCATION_STACK: ContextVar[tuple[tuple[str, str, str], ...]] = ContextVar(
    "published_agent_invocation_stack",
    default=(),
)


@dataclass(frozen=True)
class PublishedAgentRuntime:
    app: App
    release: AppRelease
    # Model config of a single-agent reference; for a cluster reference this is
    # the master model (validation only — execution goes through the orchestrator)
    model_config: ModelConfig

    @property
    def is_cluster(self) -> bool:
        return self.app.type == AppType.MULTI_AGENT

    @property
    def reference_meta(self) -> dict[str, Any]:
        return {
            "app_id": str(self.app.id),
            "release_id": str(self.release.id),
            "release_version": self.release.version,
            "version_name": self.release.version_name,
        }


@asynccontextmanager
async def _invocation_scope(app_id: uuid.UUID, release_id: uuid.UUID, kind: str = "agent"):
    key = (kind, str(app_id), str(release_id))
    stack = _INVOCATION_STACK.get()
    if key in stack:
        path = " -> ".join(f"{kind}:{app}:{release}" for kind, app, release in (*stack, key))
        raise BusinessException(f"检测到 Agent 循环调用: {path}", BizCode.BAD_REQUEST)
    if len(stack) >= _MAX_INVOCATION_DEPTH:
        raise BusinessException(
            f"Agent/工作流嵌套调用超过最大深度 {_MAX_INVOCATION_DEPTH}",
            BizCode.BAD_REQUEST,
        )

    token = _INVOCATION_STACK.set((*stack, key))
    try:
        yield
    finally:
        _INVOCATION_STACK.reset(token)


class PublishedAgentRunner:
    """Run a published Agent specified within the current workspace."""

    @staticmethod
    async def _resolve_runtime(
        db: AsyncSession,
        *,
        app_id: uuid.UUID,
        release_policy: ReleasePolicy,
        release_id: uuid.UUID | None,
        workspace_id: uuid.UUID,
    ) -> PublishedAgentRuntime:
        app = await db.get(App, app_id)
        if not app or not app.is_active:
            raise BusinessException("引用的 Agent 应用不存在或已停用", BizCode.APP_NOT_FOUND)
        if app.workspace_id != workspace_id:
            raise BusinessException("第一阶段仅支持引用当前工作空间的 Agent 应用", BizCode.WORKSPACE_NO_ACCESS)
        # Cluster references: only multi_agent apps whose published snapshot uses
        # supervisor_loop orchestration are allowed (three-phase supervisor /
        # collaboration modes depend on conversation state and don't apply here)
        if app.type == AppType.MULTI_AGENT:
            pass
        elif app.type != AppType.AGENT:
            raise BusinessException("引用的应用不是 Agent 类型", BizCode.APP_TYPE_NOT_SUPPORTED)
        if app.status != AppStatus.ACTIVE:
            raise BusinessException("引用的 Agent 应用尚未发布或已归档", BizCode.APP_NOT_PUBLISHED)

        effective_release_id = release_id
        if release_policy == "current":
            effective_release_id = app.current_release_id
        if not effective_release_id:
            raise BusinessException("引用的 Agent 应用没有可用发布版本", BizCode.APP_NOT_PUBLISHED)

        release = await db.get(AppRelease, effective_release_id)
        if (
            not release
            or not release.is_active
            or release.app_id != app.id
            or release.type != app.type
        ):
            raise BusinessException("Agent 发布版本不存在、已下线或归属错误", BizCode.RELEASE_NOT_FOUND)
        if app.type == AppType.MULTI_AGENT:
            _cfg = release.config if isinstance(release.config, dict) else {}
            if (_cfg.get("orchestration_mode") or "") != "supervisor_loop":
                raise BusinessException(
                    "仅支持引用 supervisor_loop（主管循环）模式的集群应用",
                    BizCode.APP_TYPE_NOT_SUPPORTED,
                )
        if not release.default_model_config_id:
            raise BusinessException("Agent 发布版本缺少模型配置", BizCode.AGENT_CONFIG_MISSING)

        # Runtime lookup goes through ModelConfigService: missing/deprecated entries
        # raise precise error codes here, and model_base is already joinedloaded
        model_config = await ModelConfigService.get_model_by_id_async(
            db,
            release.default_model_config_id,
            tenant_id=None,
        )
        if not model_config.is_active:
            raise BusinessException("Agent 发布版本引用的模型不存在或已停用", BizCode.NOT_FOUND)

        return PublishedAgentRuntime(app=app, release=release, model_config=model_config)

    async def run(
        self,
        *,
        app_id: uuid.UUID,
        release_policy: ReleasePolicy,
        release_id: uuid.UUID | None,
        workspace_id: uuid.UUID,
        user_id: str | None,
        message: str,
        variables: dict[str, Any],
        files: list[FileInput],
        history: list[dict[str, str]],
        storage_type: str | None = None,
        user_rag_memory_id: str | None = None,
    ) -> dict[str, Any]:
        async with get_async_db_context() as db:
            runtime = await self._resolve_runtime(
                db,
                app_id=app_id,
                release_policy=release_policy,
                release_id=release_id,
                workspace_id=workspace_id,
            )
            async with _invocation_scope(
                runtime.app.id, runtime.release.id, "cluster" if runtime.is_cluster else "agent"
            ):
                if runtime.is_cluster:
                    result = await self._run_cluster_reference(
                        db, runtime,
                        history=history,
                        message=message,
                        user_id=user_id,
                        variables=variables,
                        files=files,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                    )
                else:
                    agent_config = agent_config_4_app_release(runtime.release)
                    result = await AgentRunService(db).run(
                        agent_config=agent_config,
                        model_config=runtime.model_config,
                        message=message,
                        workspace_id=workspace_id,
                        conversation_id=None,
                        user_id=user_id,
                        variables=variables,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        web_search=True,
                        memory=True,
                        sub_agent=True,
                        files=files,
                        source="workflow_agent_reference",
                        history=history,
                        skip_save=True,
                        stateless=True,
                    )
                result["reference_meta"] = runtime.reference_meta
                return result

    async def run_stream(
        self,
        *,
        app_id: uuid.UUID,
        release_policy: ReleasePolicy,
        release_id: uuid.UUID | None,
        workspace_id: uuid.UUID,
        user_id: str | None,
        message: str,
        variables: dict[str, Any],
        files: list[FileInput],
        history: list[dict[str, str]],
        storage_type: str | None = None,
        user_rag_memory_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with get_async_db_context() as db:
            runtime = await self._resolve_runtime(
                db,
                app_id=app_id,
                release_policy=release_policy,
                release_id=release_id,
                workspace_id=workspace_id,
            )
            async with _invocation_scope(
                runtime.app.id, runtime.release.id, "cluster" if runtime.is_cluster else "agent"
            ):
                if runtime.is_cluster:
                    stream = self._run_cluster_reference_stream(
                        db, runtime,
                        history=history,
                        message=message,
                        user_id=user_id,
                        variables=variables,
                        files=files,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                    )
                else:
                    agent_config = agent_config_4_app_release(runtime.release)
                    stream = AgentRunService(db).run_stream(
                        agent_config=agent_config,
                        model_config=runtime.model_config,
                        message=message,
                        workspace_id=workspace_id,
                        conversation_id=None,
                        user_id=user_id,
                        variables=variables,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        web_search=True,
                        memory=True,
                        sub_agent=True,
                        files=files,
                        source="workflow_agent_reference",
                        history=history,
                        skip_save=True,
                        stateless=True,
                    )
                async for raw_event in stream:
                    event = raw_event if isinstance(raw_event, dict) else self._parse_sse_event(raw_event)
                    if event is None:
                        continue
                    # Cluster streaming emits sub-agent dispatch/process events
                    # (agent_dispatch/sub_agent_message, etc.) that the workflow
                    # node does not consume, so they are dropped. The supervisor's
                    # own tool calls (tool_start/tool_end/tool_error) and its
                    # iteration trace (agent_log) use the same payload shape as a
                    # single Agent and are forwarded so the workflow panel can
                    # render them.
                    if runtime.is_cluster and event["type"] not in (
                        "message", "end", "error", "tool_start", "tool_end", "tool_error",
                        "agent_log",
                    ):
                        continue
                    # Sub-agent traces carry attribution fields; only the supervisor's
                    # own trace may reach the node, otherwise a sub-agent snapshot
                    # would replace the supervisor's iteration info.
                    if (
                        runtime.is_cluster
                        and event["type"] == "agent_log"
                        and isinstance(event["data"], dict)
                        and ("agent_id" in event["data"] or "agent_name" in event["data"])
                    ):
                        continue
                    if event["type"] == "error":
                        error = event["data"].get("error")
                        if isinstance(error, dict):
                            error = error.get("message") or json.dumps(error, ensure_ascii=False)
                        raise BusinessException(str(error or "引用 Agent 执行失败"), BizCode.INTERNAL_ERROR)
                    if event["type"] == "end":
                        event["data"]["reference_meta"] = runtime.reference_meta
                    yield event

    @staticmethod
    def _normalize_cluster_sse_event(raw_event: str) -> dict[str, Any] | None:
        """Cluster SSE event -> unified dict; message events take data.content."""
        event = PublishedAgentRunner._parse_sse_event(raw_event)
        if event is None:
            return None
        if event["type"] == "message" and not isinstance(event["data"], dict):
            event["data"] = {"content": str(event["data"])}
        return event

    async def _run_cluster_reference(
        self,
        db: AsyncSession,
        runtime: PublishedAgentRuntime,
        *,
        message: str,
        user_id: str | None,
        variables: dict[str, Any],
        files: list[FileInput],
        storage_type: str | None,
        user_rag_memory_id: str | None,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Run a published cluster (supervisor_loop) in a session-less way.

        conversation_id=None -> supervisor history / opening statement / master
        execution record all degrade to empty; sub-agents go through the existing
        sub_agent=True path and already persist no conversation. The return shape
        aligns with the orchestrator.execute supervisor_loop branch
        (message/usage/citations, etc.).
        """
        from .multi_agent_orchestrator import MultiAgentOrchestrator

        config = multi_agent_config_4_app_release(runtime.release)
        orchestrator = await MultiAgentOrchestrator.create(db, config)
        # No conversation_id here, so the supervisor cannot load history itself;
        # hand it the history the calling workflow already assembled.
        orchestrator._external_history = list(history or [])
        result = await orchestrator.execute(
            message=message,
            conversation_id=None,
            user_id=user_id,
            variables=variables,
            web_search=True,
            storage_type=storage_type or "",
            user_rag_memory_id=user_rag_memory_id or "",
            files=files or None,
        )
        # Align with the Agent reference return surface: the supervisor_loop
        # branch's "message" is the final answer; usage / citations / suggested
        # questions keys are kept as-is (workflow nodes consume message/usage/
        # citations)
        result.setdefault("message", result.get("message") or result.get("content", ""))
        return result

    async def _run_cluster_reference_stream(
        self,
        db: AsyncSession,
        runtime: PublishedAgentRuntime,
        *,
        message: str,
        user_id: str | None,
        variables: dict[str, Any],
        files: list[FileInput],
        storage_type: str | None,
        user_rag_memory_id: str | None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Session-less streaming run of a published cluster (supervisor_loop);
        events are converted to unified dicts for the caller's filtering."""
        from .multi_agent_orchestrator import MultiAgentOrchestrator

        config = multi_agent_config_4_app_release(runtime.release)
        orchestrator = await MultiAgentOrchestrator.create(db, config)
        orchestrator._external_history = list(history or [])
        orchestrator._emit_supervisor_trace = True
        async for raw_event in orchestrator.execute_stream(
            message=message,
            conversation_id=None,
            user_id=user_id,
            variables=variables,
            web_search=True,
            storage_type=storage_type or "",
            user_rag_memory_id=user_rag_memory_id or "",
            files=files or None,
        ):
            event = self._normalize_cluster_sse_event(raw_event)
            if event is not None:
                yield event

    @staticmethod
    def _parse_sse_event(raw_event: str) -> dict[str, Any] | None:
        """Parse a single SSE event yielded by AgentRunService."""
        event_type: str | None = None
        data_lines: list[str] = []
        for line in raw_event.splitlines():
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not event_type:
            return None
        raw_data = "\n".join(data_lines)
        try:
            data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            data = {"content": raw_data}
        return {"type": event_type, "data": data}
