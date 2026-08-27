"""记忆验证页的读取编排服务。"""

from __future__ import annotations

import asyncio
import html
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.memory.enums import Neo4jNodeType, SearchStrategy
from app.core.memory.memory_service import MemoryService
from app.core.memory.models.service_models import MemorySearchResult
from app.core.memory.retrieval_trace.stage_events import (
    build_memory_stage_payload,
    memory_stage_collector,
)
from app.core.memory.retrieval_trace.stage_projection import build_validation_trace, project_memory_items
from app.db import get_async_db_context
from app.schemas.memory_agent_schema import UserInput
from app.services.memory_agent_service import MemoryAgentService
from app.services.memory_config_service import MemoryConfigService
from app.utils.sse_utils import format_sse_message
from app.utils.tmp_session import ChatSessionCache

DEFAULT_STORAGE_TYPE = "neo4j"
SSE_HEARTBEAT_SECONDS = 15
SSE_STAGE_POLL_SECONDS = 0.1

SEARCH_MODE_NAMES = {
    SearchStrategy.DEEP: "deep",
    SearchStrategy.NORMAL: "normal",
    SearchStrategy.QUICK: "quick",
    SearchStrategy.EXPRESS: "express",
}
VALIDATION_MEMORY_STAGE_ALLOWLIST = frozenset({
    "query_preprocessed",
    "profile_loaded",
    "query_split",
    "hybrid_searched",
    "keyword_searched",
    "perceptual_processed",
})

logger = get_api_logger()


def _format_event(event_type: str, data: dict[str, Any]) -> str:
    """使用 FastAPI 的编码器处理 UUID、枚举和 Pydantic 模型。"""
    return format_sse_message(event_type, jsonable_encoder(data))


async def _stream_heartbeats(task: asyncio.Task[Any]) -> AsyncIterator[str]:
    """长时间无业务事件时保持 SSE 连接。"""
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=SSE_HEARTBEAT_SECONDS)
        if not done:
            yield ": heartbeat\n\n"


def _build_intermediate_outputs(
        search_result: MemorySearchResult,
        search_switch: str,
) -> list[dict[str, Any]]:
    """构造记忆验证页兼容的 intermediate_outputs。"""
    intermediate_outputs: list[dict[str, Any]] = []
    sub_queries = {str(memory.query) for memory in search_result.memories}
    if search_switch in {SearchStrategy.DEEP, SearchStrategy.NORMAL}:
        intermediate_outputs.append({
            "type": "problem_split",
            "title": "问题拆分",
            "data": [
                {"id": f"Q{index}", "question": question}
                for index, question in enumerate(
                    (question for question in sub_queries if question),
                    start=1,
                )
            ],
        })

    perceptual_data = [
        memory.data
        for memory in search_result.memories
        if memory.source == Neo4jNodeType.PERCEPTUAL
    ]
    if perceptual_data:
        intermediate_outputs.append({
            "type": "perceptual_retrieve",
            "title": "感知记忆检索",
            "data": perceptual_data,
            "total": len(perceptual_data),
        })

    intermediate_outputs.append({
        "type": "search_result",
        "title": f"合并检索结果 (共{len(sub_queries)}个查询,{len(search_result.memories)}条结果)",
        "result": html.escape(search_result.content),
        "raw_result": search_result.memories,
        "total": len(search_result.memories),
    })
    return intermediate_outputs


def _build_fast_search_stage(
        strategy: SearchStrategy,
        end_user_id: str,
        search_result: MemorySearchResult,
) -> dict[str, Any] | None:
    """为未产生实时检索阶段的快速模式投影公共 memory_stage。"""
    stage = {
        SearchStrategy.QUICK: "hybrid_searched",
        SearchStrategy.EXPRESS: "keyword_searched",
    }.get(strategy)
    if stage is None:
        return None
    memories = [memory for memory in search_result.memories if memory.id != end_user_id]
    items = project_memory_items(memories, limit=3)
    execution = search_result.execution_trace
    return build_memory_stage_payload(stage=stage, data={
        "hit_count": execution.raw_hit_count if execution is not None else len(memories),
        "memory_count": len(memories),
        "shown_count": len(items),
        "items": items,
    })


class MemoryValidationService:
    """隔离记忆验证页的 SSE 与对话编排，不改共享检索链路。"""

    def __init__(
            self,
            *,
            user_input: UserInput,
            memory_service: MemoryService,
            session_cache: ChatSessionCache,
            request_id: str,
            answer_service: MemoryAgentService | None = None,
    ) -> None:
        self.user_input = user_input
        self.memory_service = memory_service
        self.session_cache = session_cache
        self.request_id = request_id
        self.answer_service = answer_service or MemoryAgentService()

    @classmethod
    async def create(
            cls,
            user_input: UserInput,
            *,
            request_id: str | None = None,
    ) -> MemoryValidationService:
        """在开流前完成参数校验和记忆服务初始化。"""
        SearchStrategy(user_input.search_switch)
        async with get_async_db_context() as db:
            memory_config_id = await MemoryConfigService(db).get_config_id_by_end_user_async(
                user_input.end_user_id
            )
        memory_service = await MemoryService.create(
            memory_config_id,
            end_user_id=user_input.end_user_id,
            draft=True,
        )
        return cls(
            user_input=user_input,
            memory_service=memory_service,
            session_cache=ChatSessionCache(user_input.session_id.hex),
            request_id=request_id or str(uuid.uuid4()),
        )

    @property
    def session_id(self) -> str:
        return self.session_cache.session_id

    async def stream(self) -> AsyncIterator[str]:
        """按 start、召回阶段、详细轨迹、回答、end 顺序输出。"""
        running_task: asyncio.Task[Any] | None = None
        user_input = self.user_input
        try:
            strategy = SearchStrategy(user_input.search_switch)
            yield _format_event("start", {
                "request_id": self.request_id,
                "session_id": self.session_id,
                "search_switch": user_input.search_switch,
                "mode": SEARCH_MODE_NAMES.get(strategy, "unknown"),
                "backend": DEFAULT_STORAGE_TYPE,
                "limit": 10,
            })

            running_task = asyncio.create_task(self.session_cache.get_history())
            async for heartbeat in _stream_heartbeats(running_task):
                yield heartbeat
            history = await running_task
            running_task = None

            with memory_stage_collector() as retrieval_stages:
                # ContextVar 会随 create_task 复制，Pipeline 仍只写入原有收集器。
                running_task = asyncio.create_task(
                    self.memory_service.read(user_input.message, strategy, history=history)
                )
                next_stage_index = 0
                event_loop = asyncio.get_running_loop()
                last_event_at = event_loop.time()
                while True:
                    while next_stage_index < len(retrieval_stages):
                        stage = retrieval_stages[next_stage_index]
                        next_stage_index += 1
                        if stage.get("stage") not in VALIDATION_MEMORY_STAGE_ALLOWLIST:
                            continue
                        last_event_at = event_loop.time()
                        yield _format_event("memory_stage", {
                            "request_id": self.request_id,
                            **stage,
                        })
                    if running_task.done():
                        break
                    await asyncio.wait({running_task}, timeout=SSE_STAGE_POLL_SECONDS)
                    if event_loop.time() - last_event_at >= SSE_HEARTBEAT_SECONDS:
                        last_event_at = event_loop.time()
                        yield ": heartbeat\n\n"
                search_result = await running_task
                running_task = None

            fast_search_stage = _build_fast_search_stage(
                strategy,
                user_input.end_user_id,
                search_result,
            )
            if fast_search_stage is not None:
                retrieval_stages.append(fast_search_stage)
                yield _format_event("memory_stage", {
                    "request_id": self.request_id,
                    **fast_search_stage,
                })

            retrieval_trace = self._build_retrieval_trace(search_result, retrieval_stages)
            yield _format_event("retrieval_trace", {
                "request_id": self.request_id,
                "trace": retrieval_trace,
            })

            intermediate_outputs = _build_intermediate_outputs(
                search_result,
                user_input.search_switch,
            )
            running_task = asyncio.create_task(
                self.answer_service.generate_summary_from_retrieve(
                    end_user_id=user_input.end_user_id,
                    retrieve_info=search_result.content,
                    history=[],
                    query=user_input.message,
                    config_id=user_input.config_id,
                )
            )
            async for heartbeat in _stream_heartbeats(running_task):
                yield heartbeat
            answer = await running_task
            running_task = None

            running_task = asyncio.create_task(
                self.session_cache.append_many([
                    {"role": "user", "content": user_input.message},
                    {"role": "assistant", "content": answer},
                ])
            )
            async for heartbeat in _stream_heartbeats(running_task):
                yield heartbeat
            await running_task
            running_task = None

            yield _format_event("message", {
                "request_id": self.request_id,
                "content": answer,
            })
            yield _format_event("end", {
                "request_id": self.request_id,
                "session_id": self.session_id,
                "intermediate_outputs": intermediate_outputs,
            })
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "Read operation error: request_id=%s, end_user=%s, error=%s",
                self.request_id,
                user_input.end_user_id,
                str(error),
                exc_info=True,
            )
            yield _format_event("error", {
                "request_id": self.request_id,
                "code": BizCode.MEMORY_READ_FAILED,
                "message": "回复对话消息失败",
            })
        finally:
            if running_task is not None and not running_task.done():
                running_task.cancel()
                with suppress(asyncio.CancelledError):
                    await running_task

    def _build_retrieval_trace(
            self,
            search_result: MemorySearchResult,
            retrieval_stages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """轨迹投影失败时降级为 null，不中断读取和回答。"""
        try:
            return build_validation_trace(
                request_id=self.request_id,
                query=self.user_input.message,
                search_switch=self.user_input.search_switch,
                end_user_id=self.user_input.end_user_id,
                result=search_result,
                collected_stages=retrieval_stages,
                backend=(
                    search_result.execution_trace.backend
                    if search_result.execution_trace
                    else DEFAULT_STORAGE_TYPE
                ),
                limit=(
                    search_result.execution_trace.limit
                    if search_result.execution_trace
                    else 10
                ),
            )
        except Exception:
            logger.warning(
                "Unable to build retrieval trace: request_id=%s, end_user=%s",
                self.request_id,
                self.user_input.end_user_id,
                exc_info=True,
            )
            return None
