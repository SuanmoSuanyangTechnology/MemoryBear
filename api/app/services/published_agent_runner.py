"""已发布 Agent 应用的无会话副作用运行器。

该服务负责解析 Agent 的 AppRelease 快照，并以 stateless 子 Agent 模式复用
AgentRunService。它不创建 Conversation，不写入 Message/AgentExecution。
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, AsyncIterator, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.db import get_async_db_context
from app.models import App, AppRelease, ModelConfig
from app.models.app_model import AppStatus, AppType
from app.schemas.app_schema import FileInput
from app.services.draft_run_service import AgentRunService
from app.services.model_service import ModelConfigService
from app.utils.app_config_utils import agent_config_4_app_release, multi_agent_config_4_app_release

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
    # 单 Agent 引用的模型配置；集群引用时为主模型（校验用途），执行走编排器
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
    """执行当前工作空间内指定的已发布 Agent。"""

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
        # 集群引用：仅放行 multi_agent 且发布快照为 supervisor_loop 编排的应用
        #（三段式 supervisor/collaboration 依赖会话态，无会话引用场景不适用）
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

        # 运行时读数走 ModelConfigService：不存在/已弃用在此精确报码，且 model_base 已 joinedload
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
                        execution_mode="in_process",
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
                        execution_mode="in_process",
                    )
                async for raw_event in stream:
                    event = raw_event if isinstance(raw_event, dict) else self._parse_sse_event(raw_event)
                    if event is None:
                        continue
                    # 集群流式有子 Agent 派发/过程事件（agent_dispatch/sub_agent_message 等），
                    # 引用场景只回传主气泡正文与终态，过程事件丢弃（工作流节点只认 message/end）
                    if runtime.is_cluster and event["type"] not in ("message", "end", "error"):
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
        """集群 SSE 事件 → 统一 dict；message 事件取 data.content。"""
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
    ) -> dict[str, Any]:
        """以无会话方式执行已发布集群（supervisor_loop）。

        conversation_id=None → 主管历史/开场白/主执行记录全部降级为空，
        子 Agent 走既有 sub_agent=True 链路本就无会话落库；返回结构对齐
        orchestrator.execute 的 supervisor_loop 分支（message/usage/citations 等）。
        """
        from app.services.multi_agent_orchestrator import MultiAgentOrchestrator

        config = multi_agent_config_4_app_release(runtime.release)
        orchestrator = await MultiAgentOrchestrator.create(db, config)
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
        # 对齐 Agent 引用返回面：编排器 supervisor_loop 分支的 "message" 即最终答案，
        # usage/引用建议问题等键原样保留（工作流节点按 message/usage/citations 消费）。
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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """无会话流式执行已发布集群（supervisor_loop），事件转为统一 dict 供上游过滤。"""
        from app.services.multi_agent_orchestrator import MultiAgentOrchestrator

        config = multi_agent_config_4_app_release(runtime.release)
        orchestrator = await MultiAgentOrchestrator.create(db, config)
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
        """解析 AgentRunService 每次 yield 的单个 SSE 事件。"""
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
